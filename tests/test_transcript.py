"""Offline tests for chapters, time windows, reads, and playback links."""

from __future__ import annotations

import pytest

from video_guide.links import LinkError, format_timestamp, link_at
from video_guide.models import CaptionKind, Sentence, Transcript
from video_guide.transcript import outline_transcript, parse_chapters, read_transcript

VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _transcript(
    cues: list[tuple[float, float, str]], *, duration: float | None = None
) -> Transcript:
    sentences = tuple(Sentence(start=start, end=end, text=text) for start, end, text in cues)
    if duration is None:
        duration = max(end for _start, end, _text in cues) if cues else 0.0
    return Transcript(
        video_id=VIDEO_ID,
        language="en",
        kind=CaptionKind.MANUAL,
        duration=duration,
        sentences=sentences,
    )


def test_parse_chapters_reads_common_description_lines() -> None:
    description = """A conversation
00:00 Intro
05:30 - The point
1:02:03 Deep dive
(12:01) Question
remember 05:30 is early
00:00:00 Start over
"""
    assert parse_chapters(description) == [
        (0.0, "Intro"),
        (330.0, "The point"),
        (721.0, "Question"),
        (3723.0, "Deep dive"),
    ]


def test_outline_uses_chapters_when_there_are_at_least_two() -> None:
    transcript = _transcript(
        [
            (0.0, 4.0, "first"),
            (10.0, 12.0, "second"),
            (20.0, 22.0, "third"),
            (30.0, 32.0, "fourth"),
            (300.0, 304.0, "later"),
        ],
        duration=700.0,
    )
    windows = outline_transcript(transcript, "00:00 Intro\n05:00 Middle\n")
    assert [window.title for window in windows] == ["Intro", "Middle"]
    assert [(window.start, window.end) for window in windows] == [(0.0, 300.0), (300.0, 700.0)]
    assert windows[0].source == "chapters"
    assert windows[0].preview == ("first", "second", "third")
    assert windows[1].preview == ("later",)


def test_outline_falls_back_to_five_minute_windows() -> None:
    transcript = _transcript([(0.0, 4.0, "opens"), (301.0, 305.0, "next")], duration=700.0)
    windows = outline_transcript(transcript, "00:00 Only one\nremember 05:30 is early\n")
    assert [(window.start, window.end) for window in windows] == [
        (0.0, 300.0),
        (300.0, 600.0),
        (600.0, 700.0),
    ]
    assert {window.source for window in windows} == {"window"}
    assert windows[0].title == "0:00–5:00"
    assert windows[0].preview == ("opens",)
    assert windows[1].preview == ("next",)
    assert windows[2].preview == ()


def test_read_returns_overlapping_cues() -> None:
    transcript = _transcript([(0.0, 10.0, "spans"), (10.0, 20.0, "next"), (20.0, 30.0, "after")])
    read = read_transcript(transcript, 8.0, 12.0)
    assert [sentence.text for sentence in read.sentences] == ["spans", "next"]
    assert read.truncated is False
    assert read.text == "spans\nnext"


def test_read_truncates_at_the_character_budget() -> None:
    transcript = _transcript([(0.0, 1.0, "a" * 100), (1.0, 2.0, "b" * 100), (2.0, 3.0, "c" * 100)])
    read = read_transcript(transcript, 0.0, 10.0, max_chars=150)
    assert read.truncated is True
    assert len(read.text) == 150
    assert read.text.startswith("a" * 100 + "\n")
    assert read.sentences[-1].text == "b" * 49

    exact = _transcript([(0.0, 1.0, "a" * 8000)])
    fitted = read_transcript(exact, 0.0, 2.0)
    assert fitted.truncated is False
    assert len(fitted.text) == 8000

    over = _transcript([(0.0, 1.0, "a" * 8001)])
    cut = read_transcript(over, 0.0, 2.0)
    assert cut.truncated is True
    assert len(cut.text) == 8000


def test_format_timestamp_matches_the_clock() -> None:
    assert format_timestamp(0) == "0:00"
    assert format_timestamp(65) == "1:05"
    assert format_timestamp(754) == "12:34"
    assert format_timestamp(3661) == "1:01:01"


def test_link_at_leads_by_five_seconds_and_uses_integer_t() -> None:
    built = link_at(VIDEO_ID, 754)
    assert built.url == "https://www.youtube.com/watch?v=abcdefghijk&t=749s"
    assert built.timestamp == "12:34"
    assert built.playback_timestamp == "12:29"
    assert built.playback_seconds == 749
    assert built.lead_seconds == 5
    assert link_at(URL, 3).url.endswith("t=0s")
    assert link_at(URL, 6).url.endswith("t=1s")


def test_link_at_ignores_a_t_param_already_on_the_url() -> None:
    built = link_at("https://www.youtube.com/watch?v=abcdefghijk&t=9999s", 754)
    assert built.url == "https://www.youtube.com/watch?v=abcdefghijk&t=749s"
    short = link_at("https://youtu.be/abcdefghijk?t=12", 10)
    assert short.url == "https://www.youtube.com/watch?v=abcdefghijk&t=5s"
    http = link_at("http://www.youtube.com/watch?v=abcdefghijk", 10)
    assert http.url == "https://www.youtube.com/watch?v=abcdefghijk&t=5s"
    shorts = link_at("https://www.youtube.com/shorts/abcdefghijk", 10)
    assert shorts.video_id == VIDEO_ID


@pytest.mark.parametrize(
    "value",
    ["javascript:alert(1)", "file:///tmp/x", "https://vimeo.com/123", "not-an-id"],
)
def test_link_at_rejects_non_youtube_and_non_http_urls(value: str) -> None:
    with pytest.raises(LinkError) as caught:
        link_at(value, 10)
    assert caught.value.code == "bad_url"


def test_link_at_rejects_negative_seconds() -> None:
    with pytest.raises(LinkError) as caught:
        link_at(VIDEO_ID, -1)
    assert caught.value.code == "bad_seconds"
