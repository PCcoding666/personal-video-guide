"""Typed models for the personal video guide's local state.

These are the integration surface between the store, the service layer, and the
host. They reject unknown fields and carry an explicit ``schema_version`` so a
state file written by a newer build is refused rather than silently misread.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CAPTION_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*$")

SCHEMA_VERSION: Literal[1] = 1

# Browser names yt-dlp accepts for --cookies-from-browser. The profile stores
# only this name. Cookie bytes are never read or written by this package.
COOKIE_BROWSERS: tuple[str, ...] = ("chrome", "firefox", "safari", "edge", "brave")


class GuideModel(BaseModel):
    """Base for guide models: reject surprise fields, strip surrounding space."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FeedbackKind(str, Enum):
    """The only feedback signals the guide records about a video."""

    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    WATCHED = "watched"
    CORRECT = "correct"


class CaptionKind(str, Enum):
    """Where a timed transcript came from. Citations should say which."""

    MANUAL = "manual"
    AUTO = "auto"
    PODCAST = "podcast"


class CurrentQuestion(GuideModel):
    """A time-bounded thing the user is currently trying to answer."""

    text: str = Field(min_length=1, max_length=2_000)
    expires_at: datetime | None = None


class Profile(GuideModel):
    """Long-lived interests and delivery preferences for one user."""

    schema_version: Literal[1] = SCHEMA_VERSION
    interests: tuple[str, ...] = ()
    current_questions: tuple[CurrentQuestion, ...] = ()
    push_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    max_cards: int = Field(default=3, ge=1, le=20)
    watch_budget_minutes: int = Field(default=30, ge=1, le=600)
    announce_when_empty: bool = False
    # Both default to unset so a profile.json written before caption support
    # still loads, and schema_version stays 1.
    ytdlp_path: str | None = None
    cookies_from_browser: str | None = None
    # Netscape cookies.txt path. The file is never opened here; only yt-dlp
    # receives the path, and caption errors do not repeat it.
    cookies_file: str | None = None
    # Tried in order when choosing one extra track besides the original.
    # The fetch still downloads at most one of these.
    preferred_caption_languages: tuple[str, ...] = ()

    @field_validator("preferred_caption_languages", mode="before")
    @classmethod
    def _split_languages(cls, value: object) -> object:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("preferred_caption_languages")
    @classmethod
    def _check_languages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 8:
            raise ValueError("at most 8 preferred caption languages")
        cleaned: list[str] = []
        for item in value:
            code = item.strip()
            if not _CAPTION_LANGUAGE.fullmatch(code):
                raise ValueError("preferred caption language must look like en or zh-Hans")
            cleaned.append(code)
        return tuple(cleaned)

    @field_validator("ytdlp_path", "cookies_file", mode="before")
    @classmethod
    def _blank_path(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("cookies_file")
    @classmethod
    def _safe_cookies_file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith("-") or any(char in value for char in ("\n", "\r", "\x00")):
            raise ValueError("cookies_file must be a file path and must not look like a flag")
        return value

    @field_validator("cookies_from_browser", mode="before")
    @classmethod
    def _normalize_browser(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip().lower()
            return stripped or None
        return value

    @field_validator("cookies_from_browser")
    @classmethod
    def _known_browser(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in COOKIE_BROWSERS:
            allowed = ", ".join(COOKIE_BROWSERS)
            raise ValueError(f"cookies_from_browser must be one of: {allowed}")
        return value


class SeenVideo(GuideModel):
    """A record that a video was already surfaced, so it is not repeated."""

    schema_version: Literal[1] = SCHEMA_VERSION
    video_id: str = Field(min_length=1, max_length=256)
    seen_at: datetime


class FeedbackEntry(GuideModel):
    """A single explicit feedback signal from the user about a video."""

    schema_version: Literal[1] = SCHEMA_VERSION
    video_id: str = Field(min_length=1, max_length=256)
    kind: FeedbackKind
    note: str | None = Field(default=None, max_length=2_000)
    created_at: datetime


class SourceKind(str, Enum):
    """The kinds of feed source the guide can follow."""

    YOUTUBE = "youtube"
    PODCAST = "podcast"


class Source(GuideModel):
    """A normalized, followable feed: a YouTube channel or a podcast RSS."""

    schema_version: Literal[1] = SCHEMA_VERSION
    kind: SourceKind
    id: str = Field(min_length=1, max_length=256)
    rss_url: str = Field(min_length=1, max_length=2_048)
    title: str | None = Field(default=None, max_length=512)
    last_checked_at: datetime | None = None


class VideoItem(GuideModel):
    """One item discovered from a feed; this is data, never an instruction."""

    source_id: str = Field(min_length=1, max_length=256)
    video_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=1_024)
    url: str = Field(min_length=1, max_length=2_048)
    published_at: datetime
    summary: str = Field(default="", max_length=8_000)
    # Podcasting 2.0 <podcast:transcript> when the feed published one.
    transcript_url: str | None = Field(default=None, max_length=2_048)


class Sentence(GuideModel):
    """One timed caption cue. Times are seconds from the start of the video."""

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)


class Transcript(GuideModel):
    """A cached caption track. ``kind`` is manual, auto, or podcast."""

    schema_version: Literal[1] = SCHEMA_VERSION
    video_id: str = Field(min_length=1, max_length=256)
    language: str = Field(default="", max_length=64)
    kind: CaptionKind
    duration: float = Field(ge=0)
    sentences: tuple[Sentence, ...] = ()


class Window(GuideModel):
    """A chapter, or a fixed time slice when the description has no chapters."""

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    title: str = Field(min_length=1, max_length=512)
    source: Literal["chapters", "window"]
    preview: tuple[str, ...] = ()


class Hit(GuideModel):
    """A lexical search hit. ``url`` is a tool-built playback link when one exists."""

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)
    score: float
    context_before: str | None = None
    context_after: str | None = None
    url: str | None = None
