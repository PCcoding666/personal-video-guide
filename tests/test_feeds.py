"""Offline tests for source normalization, fetching, and feed discovery."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from video_guide.feeds import (
    YOUTUBE_FEED_ATTEMPTS,
    YOUTUBE_FEED_BACKOFF_SECONDS,
    check_feeds,
    check_source,
    parse_podcast_rss,
    parse_youtube_atom,
)
from video_guide.fetch import DEFAULT_MAX_BYTES, Fetcher, FetchError
from video_guide.models import Source, SourceKind
from video_guide.sources import SourceError, normalize_source

CHANNEL_ID = "UCabcdefghijklmnopqrstuv"
YT_RSS = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
POD_RSS = "https://pod.example/feed.xml"

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>VID111</yt:videoId>
    <title>First video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=VID111"/>
    <published>2026-10-30T12:00:00+00:00</published>
    <media:group><media:description>Desc one</media:description></media:group>
  </entry>
  <entry>
    <yt:videoId>VID222</yt:videoId>
    <title>Second video</title>
    <published>2026-10-20T12:00:00+00:00</published>
  </entry>
</feed>
"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <guid>EP1</guid>
      <title>Episode 1</title>
      <link>https://pod.example/ep1</link>
      <pubDate>Fri, 30 Oct 2026 12:00:00 +0000</pubDate>
      <description>Pod desc</description>
    </item>
  </channel>
</rss>
"""

CHANNEL_PAGE = f'<html><body>var x = {{"channelId":"{CHANNEL_ID}","foo":1}};</body></html>'


def _response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body.encode("utf-8"))


def _fetcher(
    routes: dict[str, httpx.Response],
    *,
    allowed_hosts: frozenset[str] = frozenset(),
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return Fetcher(
        allowed_hosts=allowed_hosts,
        transport=httpx.MockTransport(handler),
        max_bytes=max_bytes,
    )


# --- sources.normalize ---------------------------------------------------------


def test_normalize_channel_link_is_offline() -> None:
    source = normalize_source(f"https://www.youtube.com/channel/{CHANNEL_ID}", fetcher=Fetcher())
    assert source.kind is SourceKind.YOUTUBE
    assert source.id == CHANNEL_ID
    assert source.rss_url == YT_RSS


def test_normalize_rss_link_becomes_a_podcast_source() -> None:
    source = normalize_source(POD_RSS, fetcher=Fetcher())
    assert source.kind is SourceKind.PODCAST
    assert source.rss_url == POD_RSS
    assert source.id.startswith("podcast:")


def test_normalize_handle_resolves_channel_id_via_one_fetch() -> None:
    fetcher = _fetcher({"https://www.youtube.com/@lenny": _response(CHANNEL_PAGE)})
    source = normalize_source("https://www.youtube.com/@lenny", fetcher=fetcher)
    assert source.kind is SourceKind.YOUTUBE
    assert source.id == CHANNEL_ID
    assert source.rss_url == YT_RSS


def test_normalize_rejects_unparseable_youtube_link() -> None:
    with pytest.raises(SourceError):
        normalize_source("https://www.youtube.com/watch?v=abc", fetcher=Fetcher())


def test_normalize_rejects_non_https() -> None:
    with pytest.raises(SourceError):
        normalize_source("http://pod.example/feed.xml", fetcher=Fetcher())


# --- fetch allow-list and size cap ---------------------------------------------


def test_fetch_rejects_non_https() -> None:
    with pytest.raises(FetchError):
        Fetcher().get("http://www.youtube.com/feeds/videos.xml")


def test_fetch_rejects_host_off_allow_list() -> None:
    with pytest.raises(FetchError):
        _fetcher({}).get("https://evil.example/feed.xml")


def test_fetch_rejects_oversized_response() -> None:
    fetcher = _fetcher({YT_RSS: _response("x" * 100)}, max_bytes=10)
    with pytest.raises(FetchError):
        fetcher.get(YT_RSS)


def test_fetch_returns_body_for_allow_listed_host() -> None:
    assert _fetcher({YT_RSS: _response("ok")}).get(YT_RSS) == b"ok"


# --- parsing -------------------------------------------------------------------


def test_parse_youtube_atom() -> None:
    items = parse_youtube_atom(ATOM.encode("utf-8"), "src")
    assert [i.video_id for i in items] == ["VID111", "VID222"]
    assert items[0].title == "First video"
    assert items[0].url == "https://www.youtube.com/watch?v=VID111"
    assert items[0].summary == "Desc one"
    assert items[0].published_at == datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


PODCAST_WITH_TRANSCRIPT = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <item>
      <guid>EP9</guid>
      <title>With transcript</title>
      <link>https://pod.example/ep9</link>
      <pubDate>Fri, 30 Oct 2026 12:00:00 +0000</pubDate>
      <description>Desc</description>
      <podcast:transcript url="https://pod.example/ep9.json" type="application/json"/>
      <podcast:transcript url="https://pod.example/ep9.vtt" type="text/vtt" language="en"/>
    </item>
  </channel>
</rss>
"""


def test_parse_podcast_rss() -> None:
    items = parse_podcast_rss(RSS.encode("utf-8"), "src")
    assert len(items) == 1
    assert items[0].video_id == "EP1"
    assert items[0].url == "https://pod.example/ep1"
    assert items[0].published_at == datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    assert items[0].transcript_url is None


def test_parse_podcast_rss_prefers_vtt_transcript() -> None:
    items = parse_podcast_rss(PODCAST_WITH_TRANSCRIPT.encode("utf-8"), "src")
    assert items[0].video_id == "EP9"
    assert items[0].transcript_url == "https://pod.example/ep9.vtt"


# --- discovery: dedup, window, isolation ---------------------------------------


def _youtube_source() -> Source:
    return Source(kind=SourceKind.YOUTUBE, id=CHANNEL_ID, rss_url=YT_RSS)


def test_check_source_skips_already_seen() -> None:
    fetcher = _fetcher({YT_RSS: _response(ATOM)})
    result = check_source(
        _youtube_source(),
        fetcher=fetcher,
        is_seen=lambda vid: vid == "VID111",
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    assert result.ok is True
    assert [v.video_id for v in result.new_videos] == ["VID222"]


def test_check_source_applies_48h_overlap_window() -> None:
    fetcher = _fetcher({YT_RSS: _response(ATOM)})
    source = _youtube_source().model_copy(
        update={"last_checked_at": datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)}
    )
    result = check_source(
        source,
        fetcher=fetcher,
        is_seen=lambda vid: False,
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    # cutoff = 2026-10-27 12:00; VID111 (10-30) kept, VID222 (10-20) dropped.
    assert [v.video_id for v in result.new_videos] == ["VID111"]


def test_check_feeds_isolates_a_failing_source() -> None:
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    good = _youtube_source()
    bad = Source(
        kind=SourceKind.PODCAST,
        id="podcast:broken",
        rss_url="https://pod.example/missing.xml",
    )
    fetcher = _fetcher({YT_RSS: _response(ATOM)}, allowed_hosts=frozenset({"pod.example"}))
    result = check_feeds(
        [good, bad],
        fetcher=fetcher,
        is_seen=lambda vid: False,
        now=now,
    )
    by_id = {c.source_id: c for c in result.per_source}
    assert by_id[good.id].ok is True
    assert by_id[good.id].next_checked_at == now
    assert by_id["podcast:broken"].ok is False
    assert by_id["podcast:broken"].next_checked_at is None
    assert {v.video_id for v in result.new_videos} == {"VID111", "VID222"}


def _counting_fetcher(statuses: list[int], body: str = ATOM) -> tuple[Fetcher, dict[str, int]]:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        status = statuses[min(calls["n"] - 1, len(statuses) - 1)]
        if status == 200:
            return httpx.Response(200, content=body.encode("utf-8"))
        return httpx.Response(status)

    return Fetcher(transport=httpx.MockTransport(handler)), calls


def test_youtube_feed_retries_404_then_succeeds() -> None:
    fetcher, calls = _counting_fetcher([404, 200])
    sleeps: list[float] = []
    result = check_source(
        _youtube_source(),
        fetcher=fetcher,
        is_seen=lambda vid: False,
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
        sleeper=sleeps.append,
    )
    assert result.ok is True
    assert calls["n"] == 2
    assert sleeps == [YOUTUBE_FEED_BACKOFF_SECONDS[0]]
    assert {video.video_id for video in result.new_videos} == {"VID111", "VID222"}


def test_youtube_feed_retries_5xx_with_bounded_backoff() -> None:
    fetcher, calls = _counting_fetcher([503, 502, 200])
    sleeps: list[float] = []
    result = check_source(
        _youtube_source(),
        fetcher=fetcher,
        is_seen=lambda vid: False,
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
        sleeper=sleeps.append,
    )
    assert result.ok is True
    assert calls["n"] == 3
    assert sleeps == list(YOUTUBE_FEED_BACKOFF_SECONDS)


def test_youtube_feed_stops_after_the_attempt_budget() -> None:
    fetcher, calls = _counting_fetcher([404, 404, 404, 200])
    sleeps: list[float] = []
    result = check_source(
        _youtube_source(),
        fetcher=fetcher,
        is_seen=lambda vid: False,
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
        sleeper=sleeps.append,
    )
    assert result.ok is False
    assert result.next_checked_at is None
    assert calls["n"] == YOUTUBE_FEED_ATTEMPTS
    assert sleeps == list(YOUTUBE_FEED_BACKOFF_SECONDS)
    assert result.error is not None
    assert "404" in result.error


def test_youtube_feed_does_not_retry_403_or_a_podcast_404() -> None:
    youtube, youtube_calls = _counting_fetcher([403, 200])
    sleeps: list[float] = []
    refused = check_source(
        _youtube_source(),
        fetcher=youtube,
        is_seen=lambda vid: False,
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
        sleeper=sleeps.append,
    )
    assert refused.ok is False
    assert youtube_calls["n"] == 1
    assert sleeps == []

    podcast_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        podcast_calls["n"] += 1
        return httpx.Response(404)

    podcast = check_source(
        Source(kind=SourceKind.PODCAST, id="podcast:x", rss_url=POD_RSS),
        fetcher=Fetcher(
            allowed_hosts=frozenset({"pod.example"}),
            transport=httpx.MockTransport(handler),
        ),
        is_seen=lambda vid: False,
        now=datetime(2026, 11, 1, tzinfo=timezone.utc),
        sleeper=sleeps.append,
    )
    assert podcast.ok is False
    assert podcast_calls["n"] == 1
    assert sleeps == []
