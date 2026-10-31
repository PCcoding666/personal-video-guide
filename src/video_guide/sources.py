"""Normalize channel/podcast links into followable feed sources.

YouTube ``/channel/UC…`` links and direct RSS URLs normalize offline. A bare
``@handle`` cannot be resolved offline, so (per the allow-list) the channel page
is fetched once solely to read its ``channelId``. Anything that cannot be
resolved raises ``SourceError`` asking the user for a clearer link — the guide
never guesses.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from .fetch import Fetcher
from .models import Source, SourceKind

_CHANNEL_ID = re.compile(r"UC[0-9A-Za-z_-]{22}")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_HANDLE = re.compile(r"^/(@[A-Za-z0-9._-]+)")
_CHANNEL_PATH = re.compile(r"^/channel/(UC[0-9A-Za-z_-]{22})")
_CHANNEL_ID_IN_HTML = re.compile(r'"(?:channelId|externalId)":"(UC[0-9A-Za-z_-]{22})"')
_CHANNEL_HREF_IN_HTML = re.compile(r"/channel/(UC[0-9A-Za-z_-]{22})")


class SourceError(Exception):
    """A source link could not be normalized into a feed."""


def _youtube_rss(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def normalize_source(raw: str, *, fetcher: Fetcher) -> Source:
    """Normalize a user-supplied link into a :class:`Source`."""
    text = raw.strip()
    if not text:
        raise SourceError("empty source link")
    candidate = text if _SCHEME.match(text) else f"https://{text}"
    parts = urlsplit(candidate)
    if parts.scheme != "https":
        raise SourceError(f"only https sources are accepted: {text!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise SourceError(f"could not parse a host from: {text!r}")

    if host == "youtube.com" or host.endswith(".youtube.com"):
        return _normalize_youtube(candidate, parts.path, parts.query, fetcher=fetcher)

    # Any other https URL is treated as a podcast RSS feed the user added.
    source_id = "podcast:" + hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:16]
    return Source(kind=SourceKind.PODCAST, id=source_id, rss_url=candidate)


def _normalize_youtube(url: str, path: str, query: str, *, fetcher: Fetcher) -> Source:
    channel_match = _CHANNEL_PATH.match(path)
    if channel_match:
        channel_id = channel_match.group(1)
        return Source(kind=SourceKind.YOUTUBE, id=channel_id, rss_url=_youtube_rss(channel_id))

    if path.startswith("/feeds/videos.xml"):
        ids = parse_qs(query).get("channel_id", [])
        if ids and _CHANNEL_ID.fullmatch(ids[0]):
            return Source(kind=SourceKind.YOUTUBE, id=ids[0], rss_url=_youtube_rss(ids[0]))
        raise SourceError(f"YouTube feed url is missing a valid channel_id: {url!r}")

    handle_match = _HANDLE.match(path)
    if handle_match:
        channel_id = _resolve_handle(handle_match.group(1), fetcher=fetcher)
        return Source(kind=SourceKind.YOUTUBE, id=channel_id, rss_url=_youtube_rss(channel_id))

    raise SourceError(
        f"unrecognized YouTube link: {url!r}; "
        "provide a /channel/UC… link, an @handle, or the RSS url"
    )


def _resolve_handle(handle: str, *, fetcher: Fetcher) -> str:
    """Resolve ``@handle`` to a channel id via a single allow-listed page GET."""
    page = fetcher.get(f"https://www.youtube.com/{handle}").decode("utf-8", "replace")
    for pattern in (_CHANNEL_ID_IN_HTML, _CHANNEL_HREF_IN_HTML, _CHANNEL_ID):
        match = pattern.search(page)
        if match is not None:
            return match.group(1) if pattern is not _CHANNEL_ID else match.group(0)
    raise SourceError(
        f"could not resolve {handle!r} to a channel id; provide the /channel/UC… link"
    )
