"""Typed models for the personal video guide's local state.

These are the integration surface between the store, the service layer, and the
host. They reject unknown fields and carry an explicit ``schema_version`` so a
state file written by a newer build is refused rather than silently misread.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION: Literal[1] = 1


class GuideModel(BaseModel):
    """Base for guide models: reject surprise fields, strip surrounding space."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FeedbackKind(str, Enum):
    """The only feedback signals the guide records about a video."""

    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    WATCHED = "watched"
    CORRECT = "correct"


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
