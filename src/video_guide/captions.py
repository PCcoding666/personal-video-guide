"""Fetch and parse timed captions.

YouTube captions are obtained by running a ``yt-dlp`` executable the user
installed. The argument list is fixed: skip the media download, write manual
and automatic subtitles, and land the files in a temporary directory that is
removed before this function returns. ``--cookies-from-browser`` is added only
when the profile names a browser. Cookie contents are never read, stored, or
logged, and yt-dlp's own stdout/stderr never leave this module.

Track choice, best first: manual subtitles, then automatic captions (an
``*-orig`` track ahead of a machine translation), then a Podcasting 2.0
transcript URL when the caller has one. Caption text is returned as data,
including text that looks like an instruction.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .fetch import Fetcher, FetchError
from .links import LinkError, require_video_id
from .models import COOKIE_BROWSERS, CaptionKind, Profile, Sentence, Transcript
from .state import GuideStore

DEFAULT_YTDLP_TIMEOUT = 120.0
SUB_LANGS = "all,-live_chat"
SUB_FORMAT = "json3/vtt"

_STAMP = r"(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}"
_ARROW = re.compile(rf"({_STAMP})\s*-->\s*({_STAMP})")
_TAG = re.compile(r"<[^>]*>")
_WROTE_MANUAL = re.compile(r"Writing video subtitles to:\s*(.+)$", re.IGNORECASE)
_WROTE_AUTO = re.compile(r"Writing video automatic captions to:\s*(.+)$", re.IGNORECASE)
_BOT_MARKERS = ("not a bot",)
_RATE_LIMIT_MARKERS = ("http error 429", "too many requests")
_IMPERSONATE_NOTE = (
    " yt-dlp can impersonate a browser when curl_cffi is installed in the same "
    "environment as yt-dlp. Install curl-cffi beside yt-dlp, or "
    "pip install 'personal-video-guide[impersonate]' when they share one environment."
)
_NO_CAPTION_MARKERS = (
    "no subtitles",
    "there are no subtitles",
    "subtitles are disabled",
    "no automatic captions",
    "no captions",
)
_EXT_RANK = {"json3": 0, "vtt": 1, "srt": 2}

Which = Callable[[str], str | None]


class RunnerTimeout(Exception):
    """The subtitle process exceeded its timeout. The message is not user-facing."""


@dataclass(frozen=True)
class CompletedRun:
    """Subprocess result. ``stderr`` is for classification inside this module only."""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], float], CompletedRun]


@dataclass(frozen=True)
class CaptionFailure:
    """A caption request that did not produce a transcript."""

    code: str
    message: str
    hint: str | None = None


@dataclass(frozen=True)
class CaptionOutcome:
    """Either a transcript or a failure. Expected failures are not raised."""

    transcript: Transcript | None = None
    failure: CaptionFailure | None = None
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.transcript is not None and self.failure is None


@dataclass(frozen=True)
class _Track:
    path: Path
    language: str
    ext: str
    kind: CaptionKind


def default_runner(argv: list[str], timeout: float) -> CompletedRun:
    """Run ``argv`` and capture its output. The child is killed on timeout.

    Stdin is closed so a prompt cannot block the call. Output is decoded as
    UTF-8 and kept on the returned object for the caller to classify; this
    function does not log it.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise RunnerTimeout("yt-dlp timed out") from exc
    code = proc.returncode if proc.returncode is not None else 1
    return CompletedRun(returncode=code, stdout=stdout or "", stderr=stderr or "")


def clean_caption_text(text: str) -> str:
    """Unescape entities, drop cue tags, and collapse whitespace.

    The words themselves are preserved, including text that addresses the model.
    """
    unescaped = html.unescape(text)
    without_tags = _TAG.sub("", unescaped)
    return re.sub(r"\s+", " ", without_tags).strip()


def clock_to_seconds(stamp: str) -> float:
    """Parse ``hh:mm:ss.mmm``, ``mm:ss.mmm``, or an SRT comma fraction."""
    cleaned = stamp.strip().replace(",", ".")
    parts = cleaned.split(":")
    if len(parts) == 3:
        hours_s, minutes_s, seconds_s = parts
    elif len(parts) == 2:
        hours_s = "0"
        minutes_s, seconds_s = parts
    else:
        raise ValueError(f"bad timestamp: {stamp}")
    return round(int(hours_s) * 3600 + int(minutes_s) * 60 + float(seconds_s), 3)


def parse_vtt(data: bytes) -> list[Sentence]:
    """Parse a WebVTT document into cues. Header, NOTE, and STYLE blocks are skipped."""
    lines = _normalize_newlines(data).split("\n")
    sentences: list[Sentence] = []
    index = 0
    total = len(lines)
    while index < total:
        stripped = lines[index].strip()
        upper = stripped.upper()
        if upper.startswith(("NOTE", "STYLE", "REGION")):
            index += 1
            while index < total and lines[index].strip() != "":
                index += 1
            continue
        match = _ARROW.search(lines[index])
        if match is None:
            index += 1
            continue
        try:
            start = clock_to_seconds(match.group(1))
            end = clock_to_seconds(match.group(2))
        except ValueError:
            index += 1
            continue
        index += 1
        buf: list[str] = []
        while index < total and lines[index].strip() != "":
            buf.append(lines[index].strip())
            index += 1
        cue = clean_caption_text(" ".join(buf))
        if not cue:
            continue
        sentences.append(Sentence(start=start, end=max(end, start), text=cue))
    return sentences


def parse_srt(data: bytes) -> list[Sentence]:
    """Parse an SRT document into cues."""
    text = _normalize_newlines(data).strip()
    if not text:
        return []
    sentences: list[Sentence] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        arrow_at = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if arrow_at is None:
            continue
        match = _ARROW.search(lines[arrow_at])
        if match is None:
            continue
        try:
            start = clock_to_seconds(match.group(1))
            end = clock_to_seconds(match.group(2))
        except ValueError:
            continue
        cue = clean_caption_text(" ".join(lines[arrow_at + 1 :]))
        if not cue:
            continue
        sentences.append(Sentence(start=start, end=max(end, start), text=cue))
    return sentences


def parse_json3(data: bytes) -> list[Sentence]:
    """Parse a YouTube json3 caption track.

    ``tStartMs`` and ``dDurationMs`` are converted to seconds at millisecond
    precision. Events with no spoken text (line breaks) are dropped. A missing
    duration ends at the next event.
    """
    try:
        payload = json.loads(data.decode("utf-8-sig", "replace"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    raw: list[tuple[float, float | None, str]] = []
    for event in events:
        if not isinstance(event, dict) or "tStartMs" not in event:
            continue
        try:
            start_ms = float(event["tStartMs"])
        except (TypeError, ValueError):
            continue
        dur_ms = _optional_float(event.get("dDurationMs"))
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        parts: list[str] = []
        for seg in segs:
            if isinstance(seg, dict):
                piece = seg.get("utf8")
                if isinstance(piece, str):
                    parts.append(piece)
        text = clean_caption_text("".join(parts))
        if text:
            raw.append((start_ms, dur_ms, text))
    sentences: list[Sentence] = []
    for index, (start_ms, dur_ms, text) in enumerate(raw):
        start = round(start_ms / 1000, 3)
        if dur_ms is not None:
            end = round((start_ms + dur_ms) / 1000, 3)
        elif index + 1 < len(raw):
            end = round(raw[index + 1][0] / 1000, 3)
        else:
            end = start
        end = max(end, start)
        sentences.append(Sentence(start=start, end=end, text=text))
    return sentences


def parse_caption_bytes(data: bytes, ext: str) -> list[Sentence]:
    """Parse caption bytes of ``ext`` (``vtt``, ``srt``, or ``json3``) into time order."""
    kind = ext.lower().lstrip(".")
    if kind == "json3":
        parsed = parse_json3(data)
    elif kind == "srt":
        parsed = parse_srt(data)
    elif kind == "vtt":
        parsed = parse_vtt(data)
    else:
        return []
    parsed.sort(key=lambda sentence: (sentence.start, sentence.end, sentence.text))
    return parsed


def fetch_youtube_captions(
    video: str,
    *,
    store: GuideStore,
    profile: Profile | None = None,
    runner: Runner | None = None,
    which: Which | None = None,
    timeout: float = DEFAULT_YTDLP_TIMEOUT,
) -> CaptionOutcome:
    """Fetch YouTube captions for ``video`` via the user's yt-dlp, or read the cache.

    ``profile`` defaults to the profile stored under ``store``.
    """
    try:
        video_id = require_video_id(video)
    except LinkError as exc:
        return _fail("bad_url", str(exc))
    cached = _cached(store, video_id)
    if cached is not None:
        return cached
    active = store.profile() if profile is None else profile
    executable, missing = _resolve_executable(active, which or shutil.which)
    if executable is None:
        return _fail("ytdlp_missing", missing or "yt-dlp was not found.")
    browser = active.cookies_from_browser
    if browser is not None and browser not in COOKIE_BROWSERS:
        allowed = ", ".join(COOKIE_BROWSERS)
        return _fail("invalid_profile", f"cookies_from_browser must be one of: {allowed}")
    cookies_file = _cookies_file_arg(active.cookies_file)
    if active.cookies_file and cookies_file is None:
        return _fail(
            "invalid_profile",
            "cookies_file must be a file path and must not look like a flag.",
        )
    if cookies_file is not None and not Path(cookies_file).is_file():
        return _fail(
            "cookies_file_missing",
            "The configured Netscape cookies file is missing or not a file.",
        )
    # A file and a browser both set: pass the file only. yt-dlp rejects the pair.
    if cookies_file is not None:
        browser = None
    run = runner or default_runner
    with tempfile.TemporaryDirectory(prefix="video-guide-captions-") as tmp:
        template = str(Path(tmp) / "%(id)s.%(ext)s")
        argv = _build_argv(executable, video_id, template, browser, cookies_file)
        try:
            completed = run(argv, timeout)
        except (RunnerTimeout, subprocess.TimeoutExpired):
            return _fail("timeout", "yt-dlp timed out before captions were written.")
        except FileNotFoundError:
            return _fail("ytdlp_missing", missing or "yt-dlp was not found.")
        except OSError:
            return _fail("ytdlp_failed", "yt-dlp could not be started.")
        outcome = _interpret(
            completed,
            Path(tmp),
            video_id,
            cookies_enabled=browser is not None or cookies_file is not None,
        )
    outcome = _without_cookies_path(outcome, cookies_file)
    if outcome.transcript is not None:
        store.save_caption(outcome.transcript)
    return outcome


def load_podcast_transcript(
    url: str,
    *,
    video_id: str,
    fetcher: Fetcher,
    store: GuideStore | None = None,
    language: str = "",
) -> CaptionOutcome:
    """GET a Podcasting 2.0 transcript through the allow-listed fetcher and parse it.

    The body is timed caption data (VTT, SRT, or JSON). It is not logged.
    """
    if store is not None:
        cached = _cached(store, video_id)
        if cached is not None:
            return cached
    try:
        body = fetcher.get(url)
    except FetchError as exc:
        return _fetch_failure(exc)
    ext = _guess_ext(url, body)
    sentences = parse_caption_bytes(body, ext)
    if not sentences:
        return _fail("no_captions", "The podcast transcript contained no timed cues.")
    transcript = _transcript(video_id, language, CaptionKind.PODCAST, sentences)
    if store is not None:
        store.save_caption(transcript)
    return CaptionOutcome(transcript=transcript)


def acquire_captions(
    video: str,
    *,
    store: GuideStore,
    transcript_url: str | None = None,
    transcript_language: str = "",
    fetcher: Fetcher | None = None,
    profile: Profile | None = None,
    runner: Runner | None = None,
    which: Which | None = None,
    timeout: float = DEFAULT_YTDLP_TIMEOUT,
) -> CaptionOutcome:
    """Resolve captions in source order: manual, automatic, then podcast transcript.

    A YouTube URL is fetched with yt-dlp. When that reports ``no_captions`` and
    the caller also has a ``podcast:transcript`` URL, that file is fetched next.
    A bot check is returned as-is: this package does not route around it.
    """
    try:
        youtube_id: str | None = require_video_id(video)
    except LinkError as exc:
        youtube_id = None
        youtube_error = str(exc)
    else:
        youtube_error = ""
    if youtube_id is not None:
        outcome = fetch_youtube_captions(
            video,
            store=store,
            profile=profile,
            runner=runner,
            which=which,
            timeout=timeout,
        )
        if outcome.ok:
            return outcome
        missed = outcome.failure is not None and outcome.failure.code == "no_captions"
        if missed and transcript_url and fetcher is not None:
            return load_podcast_transcript(
                transcript_url,
                video_id=youtube_id,
                fetcher=fetcher,
                store=store,
                language=transcript_language,
            )
        return outcome
    if transcript_url and fetcher is not None:
        return load_podcast_transcript(
            transcript_url,
            video_id=video.strip() or "podcast",
            fetcher=fetcher,
            store=store,
            language=transcript_language,
        )
    return _fail("bad_url", youtube_error or "need a YouTube video id or an http(s) YouTube url")


def _cookies_file_arg(path: str | None) -> str | None:
    """Return a path safe to place after ``--cookies``, or None when it is unset.

    Paths that start with ``-`` would be another yt-dlp flag. This function
    does not open the file.
    """
    if path is None:
        return None
    if path.startswith("-") or any(char in path for char in ("\n", "\r", "\x00")):
        return None
    return path


def _build_argv(
    executable: str,
    video_id: str,
    template: str,
    browser: str | None,
    cookies_file: str | None,
) -> list[str]:
    argv = [
        executable,
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        SUB_LANGS,
        "--sub-format",
        SUB_FORMAT,
        "--no-playlist",
        "-o",
        template,
    ]
    if cookies_file is not None:
        argv.extend(["--cookies", cookies_file])
    elif browser is not None:
        argv.extend(["--cookies-from-browser", browser])
    argv.append(f"https://www.youtube.com/watch?v={video_id}")
    return argv


def _without_cookies_path(outcome: CaptionOutcome, cookies_file: str | None) -> CaptionOutcome:
    """Drop the cookies path from any failure text. Success payloads have no path."""
    if cookies_file is None or outcome.failure is None:
        return outcome
    if cookies_file not in outcome.failure.message:
        return outcome
    cleaned = outcome.failure.message.replace(cookies_file, "the configured cookies file")
    return CaptionOutcome(
        failure=CaptionFailure(
            code=outcome.failure.code,
            message=cleaned,
            hint=outcome.failure.hint,
        )
    )


def _resolve_executable(profile: Profile, which: Which) -> tuple[str | None, str | None]:
    if profile.ytdlp_path:
        if Path(profile.ytdlp_path).is_file():
            return profile.ytdlp_path, None
        return None, (
            f"yt-dlp was not found at the configured ytdlp_path ({profile.ytdlp_path}). "
            "Install yt-dlp yourself or update the profile path. "
            "This package does not install it."
        )
    found = which("yt-dlp")
    if found:
        return found, None
    return None, (
        "yt-dlp was not found on PATH. Install yt-dlp yourself and retry, "
        "or set profile ytdlp_path to the executable. This package does not install it."
    )


def _interpret(
    completed: CompletedRun,
    directory: Path,
    video_id: str,
    *,
    cookies_enabled: bool,
) -> CaptionOutcome:
    tracks = _collect_tracks(completed.stdout, completed.stderr, directory, video_id)
    transcript = _select_transcript(tracks, video_id)
    if transcript is not None:
        return CaptionOutcome(transcript=transcript)
    blob = f"{completed.stdout}\n{completed.stderr}".casefold()
    # The sign-in wall is a bot check. HTTP 429 without that phrase is its own
    # code: live fetches from a cloud IP mostly returned 429, and the sign-in
    # page was the exception.
    if any(marker in blob for marker in _BOT_MARKERS):
        return _blocked("bot_check", cookies_enabled=cookies_enabled)
    if any(marker in blob for marker in _RATE_LIMIT_MARKERS):
        return _blocked("rate_limited", cookies_enabled=cookies_enabled)
    if completed.returncode == 0 or any(marker in blob for marker in _NO_CAPTION_MARKERS):
        return _fail("no_captions", "No caption track was written for this video.")
    return _fail("ytdlp_failed", "yt-dlp exited without writing a caption track.")


def _collect_tracks(stdout: str, stderr: str, directory: Path, video_id: str) -> list[_Track]:
    kinds = _kinds_by_name(f"{stdout}\n{stderr}")
    tracks: list[_Track] = []
    if not directory.is_dir():
        return tracks
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        parsed = _parse_track_name(path, video_id)
        if parsed is None:
            continue
        language, ext = parsed
        if language.endswith("-orig"):
            kind = CaptionKind.AUTO
        else:
            kind = kinds.get(path.name, CaptionKind.MANUAL)
        tracks.append(_Track(path=path, language=language, ext=ext, kind=kind))
    return tracks


def _kinds_by_name(text: str) -> dict[str, CaptionKind]:
    kinds: dict[str, CaptionKind] = {}
    for line in text.splitlines():
        stripped = line.strip()
        auto = _WROTE_AUTO.search(stripped)
        manual = _WROTE_MANUAL.search(stripped)
        if auto is not None:
            kinds[Path(auto.group(1).strip()).name] = CaptionKind.AUTO
        elif manual is not None:
            kinds[Path(manual.group(1).strip()).name] = CaptionKind.MANUAL
    return kinds


def _parse_track_name(path: Path, video_id: str) -> tuple[str, str] | None:
    name = path.name
    prefix = f"{video_id}."
    if not name.startswith(prefix):
        return None
    ext = path.suffix.lower().lstrip(".")
    if ext not in _EXT_RANK:
        return None
    language = name[len(prefix) : -(len(ext) + 1)]
    if not language or language.lower() == "live_chat":
        return None
    return language, ext


def _select_transcript(tracks: list[_Track], video_id: str) -> Transcript | None:
    if not tracks:
        return None
    orig_bases = {
        track.language[: -len("-orig")] for track in tracks if track.language.endswith("-orig")
    }
    ordered = sorted(tracks, key=lambda track: _rank(track, orig_bases))
    for track in ordered:
        try:
            data = track.path.read_bytes()
        except OSError:
            continue
        sentences = parse_caption_bytes(data, track.ext)
        if not sentences:
            continue
        return _transcript(video_id, track.language, track.kind, sentences)
    return None


def _rank(track: _Track, orig_bases: set[str]) -> tuple[int, int, str, int, str]:
    matches = _matches_orig(track.language, orig_bases)
    if track.kind is CaptionKind.MANUAL:
        kind_rank = 0
        lang_rank = 0 if matches else 1
    else:
        kind_rank = 1
        lang_rank = 0 if track.language.endswith("-orig") else 1
    return (kind_rank, lang_rank, track.language, _EXT_RANK.get(track.ext, 9), track.path.name)


def _matches_orig(language: str, orig_bases: set[str]) -> bool:
    if not orig_bases:
        return False
    base = language.removesuffix("-orig")
    if base in orig_bases:
        return True
    return base.split("-", 1)[0] in orig_bases


def _transcript(
    video_id: str, language: str, kind: CaptionKind, sentences: list[Sentence]
) -> Transcript:
    ordered = sorted(sentences, key=lambda sentence: (sentence.start, sentence.end, sentence.text))
    duration = max(sentence.end for sentence in ordered)
    return Transcript(
        video_id=video_id,
        language=language,
        kind=kind,
        duration=duration,
        sentences=tuple(ordered),
    )


def _cached(store: GuideStore, video_id: str) -> CaptionOutcome | None:
    loaded = store.load_caption(video_id)
    if loaded is None:
        return None
    return CaptionOutcome(transcript=loaded, cached=True)


def _blocked(code: str, *, cookies_enabled: bool) -> CaptionOutcome:
    if code == "rate_limited":
        lead = "YouTube rate-limited the caption download (HTTP 429)."
    else:
        lead = "YouTube blocked the caption request (bot check)."
    if cookies_enabled:
        message = (
            f"{lead} Cookies were enabled. The guide will not try to bypass that."
            f"{_IMPERSONATE_NOTE}"
        )
        return _fail(code, message)
    message = (
        f"{lead} Browser cookies and a cookies file are both off by default. "
        "Set profile cookies_from_browser to chrome, firefox, safari, edge, or brave, "
        "or set profile cookies_file to a Netscape cookies.txt path, and retry."
        f"{_IMPERSONATE_NOTE}"
    )
    return _fail(code, message, hint="no_cookies")


def _fail(code: str, message: str, *, hint: str | None = None) -> CaptionOutcome:
    return CaptionOutcome(failure=CaptionFailure(code=code, message=message, hint=hint))


def _fetch_failure(exc: FetchError) -> CaptionOutcome:
    text = str(exc)
    if "non-https" in text or "not allow-listed" in text:
        return _fail(
            "bad_url",
            "The podcast transcript URL was refused by the fetch allow-list.",
        )
    return _fail("no_captions", "The podcast transcript could not be fetched.")


def _guess_ext(url: str, body: bytes) -> str:
    path = url.lower().split("?", 1)[0]
    for ext in ("json3", "vtt", "srt"):
        if path.endswith("." + ext):
            return ext
    sample = body.lstrip()[:200].decode("utf-8", "replace").lstrip("\ufeff").lstrip()
    if sample.startswith(("{", "[")):
        return "json3"
    if sample.upper().startswith("WEBVTT"):
        return "vtt"
    return "srt"


def _normalize_newlines(data: bytes) -> str:
    return data.decode("utf-8-sig", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None
