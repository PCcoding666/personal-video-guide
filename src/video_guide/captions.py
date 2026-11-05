"""Fetch and parse timed captions.

YouTube captions are obtained by running a ``yt-dlp`` executable the user
installed. The argument list is fixed: skip the media download, write manual
and automatic subtitles, and land the files in a temporary directory that is
removed before this function returns. ``--cookies-from-browser`` is added only
when the profile names a browser. Cookie contents are never read, stored, or
logged, and yt-dlp's own stdout/stderr never leave this module.

Track choice, best first: the original-language manual captions, otherwise the
``*-orig`` automatic track, plus at most one preferred language from the
profile. Available languages come from ``--list-subs`` (no timedtext download);
only those codes are then requested. A Podcasting 2.0 transcript URL is next
when the caller has one. Caption text is returned as data, including text that
looks like an instruction.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .fetch import Fetcher, FetchError
from .links import LinkError, require_video_id
from .models import COOKIE_BROWSERS, CaptionKind, Profile, Sentence, Transcript
from .state import GuideStore

DEFAULT_YTDLP_TIMEOUT = 120.0
SUB_FORMAT = "json3/vtt"
# One metadata listing, then at most two timedtext downloads. A 429 is retried
# twice (5s, then 15s) before the structured rate_limited error is returned.
SUBTITLE_ATTEMPTS = 3
SUBTITLE_BACKOFF_SECONDS = (5.0, 15.0)

Sleeper = Callable[[float], None]
_LANG_CODE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*$")

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
_COOKIE_MARKERS = (
    "cookies database",
    "could not find chrome cookies",
    "could not find firefox cookies",
    "could not copy chrome cookie",
    "failed to load cookies",
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
    sleeper: Sleeper = time.sleep,
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
    cookies_enabled = browser is not None or cookies_file is not None
    with tempfile.TemporaryDirectory(prefix="video-guide-captions-") as tmp:
        directory = Path(tmp)
        template = str(directory / "%(id)s.%(ext)s")
        try:
            listed = _invoke_with_retry(
                _build_list_argv(executable, video_id, template, browser, cookies_file),
                directory,
                video_id,
                run,
                timeout,
                sleeper,
            )
        except (RunnerTimeout, subprocess.TimeoutExpired):
            return _fail("timeout", "yt-dlp timed out before captions were written.")
        except FileNotFoundError:
            return _fail("ytdlp_missing", missing or "yt-dlp was not found.")
        except OSError:
            return _fail("ytdlp_failed", "yt-dlp could not be started.")
        manual, automatic = parse_subtitle_listing(f"{listed.stdout}\n{listed.stderr}")
        languages = choose_subtitle_languages(manual, automatic, active.preferred_caption_languages)
        if not languages:
            outcome = _interpret(listed, directory, video_id, cookies_enabled=cookies_enabled)
        else:
            try:
                completed = _invoke_with_retry(
                    _build_download_argv(
                        executable, video_id, template, browser, cookies_file, languages
                    ),
                    directory,
                    video_id,
                    run,
                    timeout,
                    sleeper,
                )
            except (RunnerTimeout, subprocess.TimeoutExpired):
                return _fail("timeout", "yt-dlp timed out before captions were written.")
            except FileNotFoundError:
                return _fail("ytdlp_missing", missing or "yt-dlp was not found.")
            except OSError:
                return _fail("ytdlp_failed", "yt-dlp could not be started.")
            outcome = _interpret(completed, directory, video_id, cookies_enabled=cookies_enabled)
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
    sleeper: Sleeper = time.sleep,
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
            sleeper=sleeper,
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


def _with_cookies(
    argv: list[str], browser: str | None, cookies_file: str | None, video_id: str
) -> list[str]:
    if cookies_file is not None:
        argv.extend(["--cookies", cookies_file])
    elif browser is not None:
        argv.extend(["--cookies-from-browser", browser])
    argv.append(f"https://www.youtube.com/watch?v={video_id}")
    return argv


def _build_list_argv(
    executable: str,
    video_id: str,
    template: str,
    browser: str | None,
    cookies_file: str | None,
) -> list[str]:
    """Metadata only. ``--list-subs`` does not download timedtext tracks."""
    argv = [
        executable,
        "--skip-download",
        "--list-subs",
        "--ignore-no-formats-error",
        "--no-playlist",
        "-o",
        template,
    ]
    return _with_cookies(argv, browser, cookies_file, video_id)


def _build_download_argv(
    executable: str,
    video_id: str,
    template: str,
    browser: str | None,
    cookies_file: str | None,
    languages: Sequence[str],
) -> list[str]:
    """Download the chosen language codes only. Never ``all``."""
    argv = [
        executable,
        "--skip-download",
        "--ignore-no-formats-error",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        ",".join(languages),
        "--sub-format",
        SUB_FORMAT,
        "--no-playlist",
        "-o",
        template,
    ]
    return _with_cookies(argv, browser, cookies_file, video_id)


def parse_subtitle_listing(text: str) -> tuple[list[str], list[str]]:
    """Split yt-dlp ``--list-subs`` output into manual and automatic language codes."""
    manual: list[str] = []
    automatic: list[str] = []
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if "available automatic caption" in lowered:
            section = "auto"
            continue
        if "available subtitle" in lowered:
            section = "manual"
            continue
        if section is None or not line or lowered.startswith("language"):
            continue
        token = line.split()[0]
        if not _LANG_CODE.fullmatch(token) or token.lower() == "live_chat":
            continue
        bucket = automatic if section == "auto" else manual
        if token not in bucket:
            bucket.append(token)
    return manual, automatic


def choose_subtitle_languages(
    manual: Sequence[str],
    automatic: Sequence[str],
    preferred: Sequence[str] = (),
) -> list[str]:
    """Original track, plus at most one preferred language that is a different language.

    Manual captions for the original language win over the ``*-orig`` automatic
    track. Preferred codes are tried in order; the first available one that is
    not the same language as the original is the only extra download.
    """
    orig_autos = sorted(code for code in automatic if code.lower().endswith("-orig"))
    chosen: list[str] = []
    if orig_autos:
        base = orig_autos[0][: -len("-orig")]
        chosen.append(_match_lang(manual, base) or orig_autos[0])
    elif manual:
        preferred_manual = _first_match(manual, preferred)
        chosen.append(preferred_manual or min(manual, key=str.lower))
    elif automatic:
        plain = [code for code in automatic if not code.lower().endswith("-orig")]
        chosen.append(_first_match(plain, preferred) or min(automatic, key=str.lower))
    else:
        return []
    origin = chosen[0]
    pool = list(manual) + [code for code in automatic if code not in manual]
    for pref in preferred:
        match = _match_lang(pool, pref)
        if match is None or match in chosen or _same_language(match, origin):
            continue
        chosen.append(match)
        break
    return chosen[:2]


def _match_lang(available: Sequence[str], wanted: str) -> str | None:
    wanted_l = wanted.lower()
    exact = [code for code in available if code.lower() == wanted_l]
    if exact:
        return exact[0]
    prefixed = [
        code
        for code in available
        if code.lower().startswith(wanted_l + "-") and not code.lower().endswith("-orig")
    ]
    if not prefixed:
        return None
    prefixed.sort(key=lambda code: (len(code), code.lower()))
    return prefixed[0]


def _first_match(available: Sequence[str], preferred: Sequence[str]) -> str | None:
    for pref in preferred:
        match = _match_lang(available, pref)
        if match is not None:
            return match
    return None


def _same_language(left: str, right: str) -> bool:
    a = left.lower().removesuffix("-orig")
    b = right.lower().removesuffix("-orig")
    return a == b or a.startswith(b + "-") or b.startswith(a + "-")


def _invoke_with_retry(
    argv: list[str],
    directory: Path,
    video_id: str,
    runner: Runner,
    timeout: float,
    sleeper: Sleeper,
) -> CompletedRun:
    """Run yt-dlp, keeping a usable track and retrying only a bare HTTP 429."""
    completed: CompletedRun | None = None
    for attempt in range(SUBTITLE_ATTEMPTS):
        completed = runner(argv, timeout)
        if _directory_has_transcript(directory, video_id, completed):
            return completed
        limited = _block_code(completed) == "rate_limited"
        if not limited or attempt == SUBTITLE_ATTEMPTS - 1:
            return completed
        _clear_subtitle_files(directory)
        sleeper(SUBTITLE_BACKOFF_SECONDS[attempt])
    assert completed is not None
    return completed


def _directory_has_transcript(directory: Path, video_id: str, completed: CompletedRun) -> bool:
    tracks = _collect_tracks(completed.stdout, completed.stderr, directory, video_id)
    return _select_transcript(tracks, video_id) is not None


def _clear_subtitle_files(directory: Path) -> None:
    if not directory.is_dir():
        return
    for path in directory.iterdir():
        if path.is_file() and path.suffix.lower().lstrip(".") in _EXT_RANK:
            path.unlink(missing_ok=True)


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


def _candidate_bin_dirs() -> list[Path]:
    """Directories that may hold yt-dlp for this interpreter.

    ``Path.resolve()`` follows symlinks. A uv venv's ``bin/python`` points at
    uv's managed CPython, so the resolved parent is outside the venv and does
    not contain the venv's ``yt-dlp``. Check the unresolved parent first, then
    the installer's scripts directory and ``sys.prefix/bin``, and only then the
    resolved interpreter directory.
    """
    executable = Path(sys.executable)
    ordered: list[Path] = [executable.parent]
    scripts = sysconfig.get_path("scripts")
    if scripts:
        ordered.append(Path(scripts))
    prefix = Path(sys.prefix)
    ordered.append(prefix / "bin")
    ordered.append(prefix / "Scripts")
    ordered.append(executable.resolve().parent)
    unique: list[Path] = []
    seen: set[str] = set()
    for directory in ordered:
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        unique.append(directory)
    return unique


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
    for bindir in _candidate_bin_dirs():
        for name in ("yt-dlp", "yt-dlp.exe"):
            sibling = bindir / name
            if sibling.is_file():
                return str(sibling), None
    return None, (
        "yt-dlp was not found on PATH or next to this Python interpreter. "
        "Install yt-dlp yourself in this virtualenv, or on PATH, and retry, "
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
    block = _block_code(completed)
    if block == "cookies_unreadable":
        return _fail(
            "cookies_unreadable",
            "Could not read browser cookies. The cookie database was missing or this "
            "process was not permitted to open it. On macOS, the terminal often needs "
            "Full Disk Access before --cookies-from-browser works. Export a Netscape "
            "cookies.txt and set profile cookies_file instead.",
            hint="cookies_txt",
        )
    if block == "bot_check":
        return _blocked("bot_check", cookies_enabled=cookies_enabled)
    if block == "rate_limited":
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


def _block_code(completed: CompletedRun) -> str | None:
    blob = f"{completed.stdout}\n{completed.stderr}".casefold()
    if _cookies_unreadable(blob):
        return "cookies_unreadable"
    if any(marker in blob for marker in _BOT_MARKERS):
        return "bot_check"
    if any(marker in blob for marker in _RATE_LIMIT_MARKERS):
        return "rate_limited"
    return None


def _cookies_unreadable(blob: str) -> bool:
    if any(marker in blob for marker in _COOKIE_MARKERS):
        return True
    return "cookie" in blob and "permission denied" in blob


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
