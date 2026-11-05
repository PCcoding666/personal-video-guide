"""Tool-built playback links.

Host-built links were measured 37 seconds off the time the host displayed, so
every ``t=`` value this package returns is computed here. Playback starts
``LEAD_SECONDS`` before the requested moment and never before zero. The
human-readable clock uses the same integer seconds.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

LEAD_SECONDS = 5

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "www.youtube-nocookie.com",
    }
)


class LinkError(Exception):
    """A playback link could not be built from the given video reference."""

    def __init__(self, message: str, *, code: str = "bad_url") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PlaybackLink:
    """A deep link and the clocks that agree with it."""

    video_id: str
    seconds: float
    playback_seconds: int
    timestamp: str
    playback_timestamp: str
    lead_seconds: int
    url: str


def format_timestamp(seconds: float) -> str:
    """Format a non-negative offset as ``m:ss`` or ``h:mm:ss``."""
    total = max(0, math.floor(seconds))
    hours, remainder = divmod(int(total), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_youtube_video_id(value: str) -> str | None:
    """Return the 11-character video id from a bare id or a YouTube URL.

    Accepts watch, shorts, embed, live, and youtu.be URLs. Extra query
    parameters, including a ``t=`` already on the URL, are ignored.
    """
    text = value.strip()
    if _VIDEO_ID.fullmatch(text):
        return text
    if "://" not in text:
        return None
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    host = (parts.hostname or "").lower()
    if host == "youtu.be":
        candidate = parts.path.strip("/").split("/")[0]
        return candidate if _VIDEO_ID.fullmatch(candidate) else None
    if host not in _YOUTUBE_HOSTS:
        return None
    path = parts.path
    if path == "/watch" or path.startswith("/watch/"):
        ids = parse_qs(parts.query).get("v", [])
        if ids and _VIDEO_ID.fullmatch(ids[0]):
            return ids[0]
        return None
    for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
        if path.startswith(prefix):
            candidate = path[len(prefix) :].split("/")[0]
            return candidate if _VIDEO_ID.fullmatch(candidate) else None
    return None


def require_video_id(value: str) -> str:
    """Return a YouTube video id or raise :class:`LinkError`."""
    text = value.strip()
    if not text:
        raise LinkError("need a YouTube video id or an http(s) YouTube url")
    if "://" in text and urlsplit(text).scheme.lower() not in {"http", "https"}:
        raise LinkError("refusing non-http(s) url")
    video_id = parse_youtube_video_id(text)
    if video_id is None:
        raise LinkError("need a YouTube video id or an http(s) YouTube url")
    return video_id


def link_at(video: str, seconds: float) -> PlaybackLink:
    """Build a YouTube watch URL that starts ``LEAD_SECONDS`` before ``seconds``.

    ``seconds`` is the moment the quote begins. The URL's ``t=`` is the
    playback start, floored to a whole second and clamped at zero. ``timestamp``
    is the clock for the requested moment; ``playback_timestamp`` is the clock
    for the link.
    """
    if not math.isfinite(seconds) or seconds < 0:
        raise LinkError("seconds must be a finite number >= 0", code="bad_seconds")
    video_id = require_video_id(video)
    playback = max(0.0, seconds - LEAD_SECONDS)
    playback_seconds = math.floor(playback)
    url = f"https://www.youtube.com/watch?v={video_id}&t={playback_seconds}s"
    return PlaybackLink(
        video_id=video_id,
        seconds=seconds,
        playback_seconds=playback_seconds,
        timestamp=format_timestamp(seconds),
        playback_timestamp=format_timestamp(float(playback_seconds)),
        lead_seconds=LEAD_SECONDS,
        url=url,
    )
