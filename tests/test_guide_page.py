"""Offline tests for the local guide page and outline metadata. No browser is opened."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from video_guide.guide_page import render_guide_html, shutdown_guide_server
from video_guide.models import CaptionKind, Sentence, Transcript
from video_guide.service import GuideService
from video_guide.state import GuideStore

VIDEO_ID = "abcdefghijk"
QUOTE = "We shipped it after a year."


def _store(tmp_path: Path) -> GuideStore:
    store = GuideStore(tmp_path)
    store.save_caption(
        Transcript(
            video_id=VIDEO_ID,
            language="en",
            kind=CaptionKind.MANUAL,
            duration=400,
            title="A talk",
            description="0:00 Intro\n5:00 The point\n",
            sentences=(
                Sentence(start=1, end=4, text=QUOTE),
                Sentence(start=300, end=304, text="The point is to watch it."),
            ),
        )
    )
    return store


def _segments() -> list[dict[str, object]]:
    return [
        {
            "start": 1,
            "end": 20,
            "title": "Why they shipped",
            "reason": "This is the claim you asked about.",
            "quotes": [QUOTE, "invented quote that is not in the captions"],
        },
        {
            "start": 300,
            "end": 340,
            "title": "The point",
            "reason": "The later section answers the follow-up.",
            "quotes": ["The point is to watch it."],
        },
    ]


def test_outline_uses_the_cached_description_when_none_is_passed(tmp_path: Path) -> None:
    result = GuideService(_store(tmp_path)).transcript_outline(VIDEO_ID)
    assert result["title"] == "A talk"
    assert [section["title"] for section in result["sections"]] == ["Intro", "The point"]
    assert result["sections"][0]["source"] == "chapters"


def test_open_guide_writes_json_and_serves_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_open(_url: str) -> None:
        raise AssertionError("browser should stay closed")

    monkeypatch.setattr("webbrowser.open", fail_open)
    store = _store(tmp_path)
    try:
        opened = GuideService(store).open_guide(
            VIDEO_ID,
            why="Watch the claim, then the reason.",
            segments=_segments(),
            answers=[
                {"seconds": 300, "question": "What is the point?", "text": "Watch that part."}
            ],
            open_browser=False,
        )
        assert opened["ok"] is True
        assert opened["dropped_quotes"] == 1
        assert opened["segment_count"] == 2
        document = json.loads(Path(opened["guide_path"]).read_text(encoding="utf-8"))
        assert document["video_id"] == VIDEO_ID
        assert document["url"] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
        assert document["segments"][0]["quotes"] == [QUOTE]
        assert document["segments"][0]["url"].startswith("https://www.youtube.com/watch?")
        assert document["outline"][0]["title"] == "Intro"
        assert document["sentences"][0]["text"] == QUOTE
        assert document["answers"][0]["timestamp"] == "5:00"
        page = urllib.request.urlopen(opened["url"], timeout=2).read().decode("utf-8")
        assert "Play all" in page
        assert "Why they shipped" in page
        assert "next suggested: jump to" in page
        assert "Watch the claim, then the reason." in page
        guide_url = opened["url"] + "guide.json"
        served = json.loads(urllib.request.urlopen(guide_url, timeout=2).read().decode("utf-8"))
        assert served["title"] == "A talk"
        body = json.dumps({"video_id": VIDEO_ID, "useful": False}).encode("utf-8")
        request = urllib.request.Request(
            opened["url"] + "feedback",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 200
        feedback = store.feedback_path.read_text(encoding="utf-8")
        assert "not_interested" in feedback
        assert VIDEO_ID in feedback
    finally:
        shutdown_guide_server()


def test_open_guide_rejects_a_quote_only_page_without_a_real_range(tmp_path: Path) -> None:
    result = GuideService(_store(tmp_path)).open_guide(
        VIDEO_ID,
        why="Nothing timed.",
        segments=[{"start": 900, "end": 910, "title": "Past the end", "reason": "no"}],
        open_browser=False,
    )
    assert result["ok"] is False
    assert result["error"] == "bad_segments"


def test_render_escapes_markup_in_guide_json() -> None:
    html = render_guide_html(
        {
            "video_id": VIDEO_ID,
            "title": "<script>alert(1)</script>",
            "url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
            "why": "Because.",
            "segments": [],
            "outline": [],
            "sentences": [],
            "answers": [],
        }
    )
    assert "<script>alert" not in html
    assert "\\u003cscript>" in html


def test_feedback_for_another_video_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        opened = GuideService(store).open_guide(
            VIDEO_ID,
            why="One segment.",
            segments=_segments()[:1],
            open_browser=False,
        )
        body = json.dumps({"video_id": "zzzzzzzzzzz", "useful": True}).encode("utf-8")
        request = urllib.request.Request(
            opened["url"] + "feedback",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        assert caught.value.code == 400
        assert not store.feedback_path.exists()
    finally:
        shutdown_guide_server()
