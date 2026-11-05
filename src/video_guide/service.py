"""Shared entry point for the CLI.

The service returns plain dicts so a later MCP server can register the same
functions. Caption text that looks like an instruction is passed through
unchanged. Playback URLs are attached here, via :func:`video_guide.links.link_at`,
and nowhere else.
"""

from __future__ import annotations

import math
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
        sections = []
        for window in outline_transcript(transcript, description):
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
        ytdlp_path: str | None = None,
        set_ytdlp_path: bool = False,
    ) -> dict[str, Any]:
        if not set_cookies and not set_ytdlp_path:
            return {"ok": False, "error": "invalid_profile", "message": "Nothing to update."}
        data = self.store.profile().model_dump()
        if set_cookies:
            data["cookies_from_browser"] = cookies_from_browser
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

    def _acquire(self, video: str) -> CaptionOutcome:
        return acquire_captions(
            video,
            store=self.store,
            runner=self._runner,
            which=self._which,
            timeout=self.timeout,
        )


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
