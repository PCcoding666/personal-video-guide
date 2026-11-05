"""Discover new videos from YouTube Atom and podcast RSS feeds.

Parsing is defensive: entries missing required fields are skipped rather than
aborting the feed. Each source is checked in isolation so one failure never
hides another source's results, and a failed source does not advance its
checkpoint. New items are those published after ``last_checked_at`` minus a 48h
overlap window and not already marked seen. Feed text is treated as data.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .fetch import Fetcher
from .models import Source, SourceKind, VideoItem

OVERLAP = timedelta(hours=48)

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_PODCAST_NS = (
    "{https://podcastindex.org/namespace/1.0}",
    "{https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md}",
)


@dataclass
class SourceCheck:
    """The outcome of checking one source."""

    source_id: str
    ok: bool
    new_videos: list[VideoItem] = field(default_factory=list)
    error: str | None = None
    next_checked_at: datetime | None = None


@dataclass
class FeedCheck:
    """Aggregate outcome across all checked sources."""

    new_videos: list[VideoItem]
    per_source: list[SourceCheck]


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def parse_youtube_atom(xml: bytes, source_id: str) -> list[VideoItem]:
    """Parse a YouTube channel Atom feed into video items."""
    root = ET.fromstring(xml)
    items: list[VideoItem] = []
    for entry in root.findall(f"{_ATOM}entry"):
        video_id = _text(entry.find(f"{_YT}videoId"))
        published = _text(entry.find(f"{_ATOM}published"))
        if not video_id or not published:
            continue
        title = _text(entry.find(f"{_ATOM}title")) or video_id
        url = f"https://www.youtube.com/watch?v={video_id}"
        link = entry.find(f"{_ATOM}link")
        if link is not None and link.get("href"):
            url = link.get("href", url)
        summary = ""
        group = entry.find(f"{_MEDIA}group")
        if group is not None:
            summary = _text(group.find(f"{_MEDIA}description"))
        items.append(
            VideoItem(
                source_id=source_id,
                video_id=video_id,
                title=title,
                url=url,
                published_at=_aware(datetime.fromisoformat(published)),
                summary=summary[:8_000],
            )
        )
    return items


def _podcast_transcript_url(item: ET.Element) -> str | None:
    """Pick the best Podcasting 2.0 transcript URL on one item, if any.

    VTT is preferred, then SRT, then JSON. The URL is data for a later caption
    fetch; this function does not download it.
    """
    ranked: list[tuple[int, str]] = []
    nodes: list[ET.Element] = []
    for namespace in _PODCAST_NS:
        nodes.extend(item.findall(f"{namespace}transcript"))
    for node in nodes:
        url = (node.get("url") or "").strip()
        if not url:
            continue
        mime = (node.get("type") or "").lower()
        path = url.lower().split("?", 1)[0]
        if "vtt" in mime or path.endswith(".vtt"):
            rank = 0
        elif "srt" in mime or path.endswith(".srt"):
            rank = 1
        elif "json" in mime:
            rank = 2
        else:
            rank = 3
        ranked.append((rank, url))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1]


def parse_podcast_rss(xml: bytes, source_id: str) -> list[VideoItem]:
    """Parse a podcast RSS feed into video items."""
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if channel is None:
        return []
    items: list[VideoItem] = []
    for item in channel.findall("item"):
        identifier = _text(item.find("guid")) or _text(item.find("link"))
        published = _text(item.find("pubDate"))
        if not identifier or not published:
            continue
        link = _text(item.find("link"))
        items.append(
            VideoItem(
                source_id=source_id,
                video_id=identifier,
                title=_text(item.find("title")) or identifier,
                url=link or f"podcast:{identifier}",
                published_at=_aware(parsedate_to_datetime(published)),
                summary=_text(item.find("description"))[:8_000],
                transcript_url=_podcast_transcript_url(item),
            )
        )
    return items


def check_source(
    source: Source,
    *,
    fetcher: Fetcher,
    is_seen: Callable[[str], bool],
    now: datetime,
    overlap: timedelta = OVERLAP,
) -> SourceCheck:
    """Fetch and parse one source, returning fresh, unseen items in isolation."""
    try:
        raw = fetcher.get(source.rss_url)
        if source.kind is SourceKind.YOUTUBE:
            parsed = parse_youtube_atom(raw, source.id)
        else:
            parsed = parse_podcast_rss(raw, source.id)
    except Exception as exc:  # noqa: BLE001  # one source must not sink the others
        return SourceCheck(source_id=source.id, ok=False, error=str(exc))

    cutoff = source.last_checked_at - overlap if source.last_checked_at is not None else None
    fresh = [
        item
        for item in parsed
        if (cutoff is None or item.published_at > cutoff) and not is_seen(item.video_id)
    ]
    return SourceCheck(source_id=source.id, ok=True, new_videos=fresh, next_checked_at=now)


def check_feeds(
    sources: Iterable[Source],
    *,
    fetcher: Fetcher,
    is_seen: Callable[[str], bool],
    now: datetime,
    overlap: timedelta = OVERLAP,
) -> FeedCheck:
    """Check every source, aggregating new items and per-source outcomes."""
    checks: Sequence[SourceCheck] = [
        check_source(source, fetcher=fetcher, is_seen=is_seen, now=now, overlap=overlap)
        for source in sources
    ]
    new_videos = [video for check in checks if check.ok for video in check.new_videos]
    return FeedCheck(new_videos=new_videos, per_source=list(checks))
