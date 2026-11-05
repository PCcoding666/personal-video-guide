"""Shared entry point for the CLI.

The service returns plain dicts so a later MCP server can register the same
functions. Caption text that looks like an instruction is passed through
unchanged. Playback URLs are attached here, via :func:`video_guide.links.link_at`,
and nowhere else.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .captions import (
    DEFAULT_YTDLP_TIMEOUT,
    CaptionFailure,
    CaptionOutcome,
    Runner,
    Which,
    acquire_captions,
)
from .links import LinkError, format_timestamp, link_at
from .models import Hit, Profile, Sentence, Transcript
from .search import SearchResult, search_transcript
from .state import GuideStore
from .transcript import outline_transcript, read_transcript


class GuideService:
    """Caption, outline, read, search, link, and profile operations."""

    def __init__(
        self,
        store: GuideStore | None = None,
        *,
        runner: Runner | None = None,
        which: Which | None = None,
        timeout: float = DEFAULT_YTDLP_TIMEOUT,
    ) -> None:
        self.store = store if store is not None else GuideStore()
        self._runner = runner
        self._which = which
        self.timeout = timeout

    def transcript_get(self, video: str) -> dict[str, Any]:
        """Return caption metadata. The cue text stays on disk."""
        outcome = self._acquire(video)
        if outcome.failure is not None or outcome.transcript is None:
            return _failure(outcome.failure)
        transcript = outcome.transcript
        return {
            "ok": True,
            "video_id": transcript.video_id,
            "language": transcript.language,
            "kind": transcript.kind.value,
            "duration": transcript.duration,
            "sentence_count": len(transcript.sentences),
            "cached": outcome.cached,
        }

    def transcript_outline(self, video: str, description: str = "") -> dict[str, Any]:
        outcome = self._acquire(video)
        if outcome.failure is not None or outcome.transcript is None:
            return _failure(outcome.failure)
        transcript = outcome.transcript
        chapter_source = description.strip() or transcript.description
        sections = []
        for window in outline_transcript(transcript, chapter_source):
            sections.append(
                {
                    "start": window.start,
                    "end": window.end,
                    "timestamp": format_timestamp(window.start),
                    "end_timestamp": format_timestamp(window.end),
                    "title": window.title,
                    "source": window.source,
                    "preview": list(window.preview),
                    "url": _url(transcript.video_id, window.start),
                }
            )
        return {
            "ok": True,
            "video_id": transcript.video_id,
            "title": transcript.title,
            "language": transcript.language,
            "kind": transcript.kind.value,
            "sections": sections,
        }

    def transcript_read(self, video: str, start: float, end: float) -> dict[str, Any]:
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            return {
                "ok": False,
                "error": "bad_range",
                "message": "Read needs a finite end second greater than the start second.",
            }
        outcome = self._acquire(video)
        if outcome.failure is not None or outcome.transcript is None:
            return _failure(outcome.failure)
        transcript = outcome.transcript
        read = read_transcript(transcript, start, end)
        return {
            "ok": True,
            "video_id": transcript.video_id,
            "language": transcript.language,
            "kind": transcript.kind.value,
            "start": read.start,
            "end": read.end,
            "truncated": read.truncated,
            "text": read.text,
            "url": _url(transcript.video_id, read.start),
            "sentences": [_sentence_payload(transcript, sentence) for sentence in read.sentences],
        }

    def search(self, video: str, phrases: list[str]) -> dict[str, Any]:
        outcome = self._acquire(video)
        if outcome.failure is not None or outcome.transcript is None:
            return _failure(outcome.failure)
        transcript = outcome.transcript
        found = search_transcript(transcript, phrases)
        return {
            "ok": True,
            "video_id": transcript.video_id,
            "language": transcript.language,
            "kind": transcript.kind.value,
            "cached": outcome.cached,
            "phrases": [phrase.strip() for phrase in phrases if phrase and phrase.strip()],
            "coverage": _coverage(found),
            "hits": [_hit_payload(hit) for hit in found.hits],
        }

    def link(self, video: str, seconds: float) -> dict[str, Any]:
        try:
            built = link_at(video, seconds)
        except LinkError as exc:
            return {"ok": False, "error": exc.code, "message": str(exc)}
        return {
            "ok": True,
            "video_id": built.video_id,
            "seconds": built.seconds,
            "timestamp": built.timestamp,
            "playback_seconds": built.playback_seconds,
            "playback_timestamp": built.playback_timestamp,
            "lead_seconds": built.lead_seconds,
            "url": built.url,
        }

    def show_profile(self) -> dict[str, Any]:
        return _profile_payload(self.store.profile())

    def update_profile(
        self,
        *,
        cookies_from_browser: str | None = None,
        set_cookies: bool = False,
        cookies_file: str | None = None,
        set_cookies_file: bool = False,
        caption_language: str | None = None,
        set_caption_language: bool = False,
        ytdlp_path: str | None = None,
        set_ytdlp_path: bool = False,
    ) -> dict[str, Any]:
        if not any((set_cookies, set_cookies_file, set_caption_language, set_ytdlp_path)):
            return {"ok": False, "error": "invalid_profile", "message": "Nothing to update."}
        data = self.store.profile().model_dump()
        if set_cookies:
            data["cookies_from_browser"] = cookies_from_browser
        if set_cookies_file:
            data["cookies_file"] = cookies_file
        if set_caption_language:
            data["preferred_caption_languages"] = (
                () if caption_language is None else caption_language
            )
        if set_ytdlp_path:
            data["ytdlp_path"] = ytdlp_path
        try:
            updated = Profile.model_validate(data)
        except ValidationError as exc:
            return {
                "ok": False,
                "error": "invalid_profile",
                "message": _validation_message(exc),
            }
        self.store.set_profile(updated)
        return _profile_payload(updated)

    def open_guide(
        self,
        video: str,
        *,
        why: str,
        segments: list[Mapping[str, Any]],
        answers: list[Mapping[str, Any]] | None = None,
        open_browser: bool = False,
    ) -> dict[str, Any]:
        """Write a watch-along page from captions already on disk, then serve it locally.

        Segment times and quotes come from the caller. Quotes that do not appear
        in the cached captions are dropped. The page embeds YouTube; this
        package does not download the video.
        """
        from .guide_page import publish_guide

        try:
            video_id = link_at(video, 0).video_id
        except LinkError as exc:
            return {"ok": False, "error": exc.code, "message": str(exc)}
        outcome = self._acquire(video_id)
        if outcome.failure is not None or outcome.transcript is None:
            return _failure(outcome.failure)
        transcript = outcome.transcript
        timeline = _timeline(transcript)
        kept, dropped_quotes, dropped_segments = _normalize_segments(transcript, segments, timeline)
        if not kept:
            return {
                "ok": False,
                "error": "bad_segments",
                "message": "Each segment needs a start and a later end inside the caption timeline.",
            }
        document = _guide_document(
            transcript,
            why=why,
            segments=kept,
            answers=_normalize_answers(transcript, answers or [], timeline),
        )
        page_url = publish_guide(document, store=self.store, open_browser=open_browser)
        guide_path = self.store.home / "guides" / f"{transcript.video_id}.json"
        return {
            "ok": True,
            "video_id": transcript.video_id,
            "title": transcript.title,
            "url": page_url,
            "guide_path": str(guide_path),
            "segment_count": len(kept),
            "dropped_quotes": dropped_quotes,
            "dropped_segments": dropped_segments,
        }

    def _acquire(self, video: str) -> CaptionOutcome:
        return acquire_captions(
            video,
            store=self.store,
            runner=self._runner,
            which=self._which,
            timeout=self.timeout,
        )


def _guide_document(
    transcript: Transcript,
    *,
    why: str,
    segments: list[dict[str, Any]],
    answers: list[dict[str, Any]],
) -> dict[str, Any]:
    sections = []
    for window in outline_transcript(transcript, transcript.description):
        sections.append(
            {
                "start": window.start,
                "end": window.end,
                "timestamp": format_timestamp(window.start),
                "title": window.title,
                "source": window.source,
            }
        )
    sentences = [
        {"start": sentence.start, "end": sentence.end, "text": sentence.text}
        for sentence in transcript.sentences[:8_000]
    ]
    return {
        "video_id": transcript.video_id,
        "url": f"https://www.youtube.com/watch?v={transcript.video_id}",
        "title": transcript.title,
        "why": why.strip()[:4_000],
        "duration": transcript.duration,
        "language": transcript.language,
        "kind": transcript.kind.value,
        "segments": segments,
        "outline": sections,
        "sentences": sentences,
        "answers": answers,
    }


def _timeline(transcript: Transcript) -> float:
    timeline = transcript.duration
    if transcript.sentences:
        timeline = max(timeline, max(sentence.end for sentence in transcript.sentences))
    return timeline


def _normalize_segments(
    transcript: Transcript, segments: list[Mapping[str, Any]], timeline: float
) -> tuple[list[dict[str, Any]], int, int]:
    haystack = _caption_haystack(transcript)
    kept: list[dict[str, Any]] = []
    dropped_quotes = 0
    dropped_segments = 0
    for raw in segments:
        start = _finite(raw.get("start"))
        end = _finite(raw.get("end"))
        if start is None or end is None or start < 0 or start > timeline:
            dropped_segments += 1
            continue
        if timeline > 0:
            end = min(end, timeline)
        if end <= start:
            dropped_segments += 1
            continue
        quotes: list[str] = []
        for quote in list(raw.get("quotes") or [])[:8]:
            text = str(quote).strip()
            if not text:
                continue
            if _collapsed(text) in haystack:
                quotes.append(text[:500])
            else:
                dropped_quotes += 1
        kept.append(
            {
                "start": start,
                "end": end,
                "title": str(raw.get("title") or format_timestamp(start))[:200],
                "reason": str(raw.get("reason") or "")[:1_000],
                "quotes": quotes[:4],
                "timestamp": format_timestamp(start),
                "end_timestamp": format_timestamp(end),
                "url": _url(transcript.video_id, start),
            }
        )
    return kept, dropped_quotes, dropped_segments


def _normalize_answers(
    transcript: Transcript, answers: list[Mapping[str, Any]], timeline: float
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for raw in answers:
        seconds = _finite(raw.get("seconds"))
        if seconds is None or seconds < 0 or seconds > timeline:
            continue
        kept.append(
            {
                "seconds": seconds,
                "timestamp": format_timestamp(seconds),
                "question": str(raw.get("question") or "")[:500],
                "text": str(raw.get("text") or "")[:2_000],
                "url": _url(transcript.video_id, seconds),
            }
        )
    return kept


def _caption_haystack(transcript: Transcript) -> str:
    return " ".join(_collapsed(sentence.text) for sentence in transcript.sentences)


def _collapsed(text: str) -> str:
    return " ".join(text.casefold().split())


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _failure(failure: CaptionFailure | None) -> dict[str, Any]:
    if failure is None:
        return {
            "ok": False,
            "error": "no_captions",
            "message": "No caption track was written for this video.",
        }
    payload: dict[str, Any] = {"ok": False, "error": failure.code, "message": failure.message}
    if failure.hint:
        payload["hint"] = failure.hint
    return payload


def _sentence_payload(transcript: Transcript, sentence: Sentence) -> dict[str, Any]:
    return {
        "start": sentence.start,
        "end": sentence.end,
        "timestamp": format_timestamp(sentence.start),
        "text": sentence.text,
        "url": _url(transcript.video_id, sentence.start),
    }


def _hit_payload(hit: Hit) -> dict[str, Any]:
    return {
        "start": hit.start,
        "end": hit.end,
        "timestamp": format_timestamp(hit.start),
        "end_timestamp": format_timestamp(hit.end),
        "text": hit.text,
        "context_before": hit.context_before,
        "context_after": hit.context_after,
        "score": round(hit.score, 6),
        "url": hit.url,
    }


def _coverage(found: SearchResult) -> dict[str, Any]:
    return {
        "start": found.coverage_start,
        "end": found.coverage_end,
        "sentence_count": found.sentence_count,
    }


def _url(video_id: str, seconds: float) -> str | None:
    try:
        return link_at(video_id, seconds).url
    except LinkError:
        return None


def _profile_payload(profile: Profile) -> dict[str, Any]:
    body = profile.model_dump(mode="json")
    body["ok"] = True
    return body


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid profile."
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid value"))
    return f"{loc}: {message}" if loc else message
