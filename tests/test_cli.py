"""Offline CLI tests for the watching loop. No network and no real yt-dlp."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from video_guide.cli import app

VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
QUOTE = "We shipped it after a year of building in private."

RUNNER = CliRunner()


@pytest.fixture
def guide_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("VIDSNAP_GUIDE_HOME", str(home))
    return home


def _install_fake_ytdlp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "runs.log"
    monkeypatch.setenv("GUIDE_FAKE_LOG", str(log))
    script = tmp_path / "yt-dlp"
    script.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

args = sys.argv[1:]
forbidden = {
    "--extract-audio", "-x", "--audio-format", "--format", "-f", "--cookies",
}
if any(arg in forbidden for arg in args) or "--skip-download" not in args:
    raise SystemExit(2)
if "--ignore-no-formats-error" not in args or "--cookies-from-browser" in args:
    raise SystemExit(2)
if "-J" in args:
    print(
        '{"title":"We shipped it after a year","description":"","language":"bn",'
        '"subtitles":{"en":[{"ext":"vtt"}]},"automatic_captions":{}}'
    )
    raise SystemExit(0)
log = os.environ.get("GUIDE_FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("run\\n")
out = args[args.index("-o") + 1]
directory = pathlib.Path(out).parent
target = directory / "abcdefghijk.en.vtt"
target.write_text(
    "WEBVTT\\n\\n"
    "00:02:05.000 --> 00:02:08.000\\n"
    "We shipped it after a year of building in private.\\n\\n"
    "00:02:08.000 --> 00:02:11.000\\n"
    "Then we opened it up.\\n",
    encoding="utf-8",
)
print(f"Writing video subtitles to: {target}")
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _payload(result: Any) -> dict[str, Any]:
    return json.loads(result.stdout)


def test_cli_watching_loop_on_a_fixture_executable(
    tmp_path: Path, guide_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _install_fake_ytdlp(tmp_path, monkeypatch)
    configured = RUNNER.invoke(app, ["profile", "set", "--ytdlp-path", str(script)])
    assert configured.exit_code == 0

    first = RUNNER.invoke(app, ["transcript", "get", URL])
    assert first.exit_code == 0
    got = _payload(first)
    assert got["ok"] is True
    assert got["video_id"] == VIDEO_ID
    assert got["kind"] == "manual"
    assert got["language"] == "en"
    assert got["sentence_count"] == 2
    assert got["cached"] is False
    assert "shipped" not in first.stdout

    second = RUNNER.invoke(app, ["transcript", "get", URL])
    assert second.exit_code == 0
    assert _payload(second)["cached"] is True

    found = RUNNER.invoke(
        app,
        ["search", URL, "shipped it after a year", "--phrase", "building in private"],
    )
    assert found.exit_code == 0
    body = _payload(found)
    hits = body["hits"]
    assert isinstance(hits, list)
    assert hits[0]["text"] == QUOTE
    assert hits[0]["start"] == 125.0
    assert hits[0]["timestamp"] == "2:05"
    assert hits[0]["context_after"] == "Then we opened it up."
    assert hits[0]["url"] == "https://www.youtube.com/watch?v=abcdefghijk&t=120s"
    assert body["kind"] == "manual"

    linked = RUNNER.invoke(app, ["link", URL, "125"])
    assert linked.exit_code == 0
    link_body = _payload(linked)
    assert link_body["url"] == "https://www.youtube.com/watch?v=abcdefghijk&t=120s"
    assert link_body["timestamp"] == "2:05"
    assert link_body["playback_timestamp"] == "2:00"
    assert link_body["playback_seconds"] == 120
    assert link_body["lead_seconds"] == 5

    outlined = RUNNER.invoke(app, ["transcript", "outline", URL])
    assert outlined.exit_code == 0
    sections = _payload(outlined)["sections"]
    assert isinstance(sections, list)
    assert sections[0]["source"] == "window"
    assert sections[0]["preview"][0] == QUOTE
    assert sections[0]["url"] == "https://www.youtube.com/watch?v=abcdefghijk&t=0s"

    read = RUNNER.invoke(app, ["transcript", "read", URL, "120", "130"])
    assert read.exit_code == 0
    read_body = _payload(read)
    assert read_body["truncated"] is False
    assert QUOTE in str(read_body["text"])
    assert read_body["sentences"][0]["start"] == 125.0
    assert read_body["sentences"][0]["url"].endswith("t=120s")

    # get, a second get, search, outline, and read share one caption fetch.
    assert (tmp_path / "runs.log").read_text(encoding="utf-8") == "run\n"


def test_cli_link_does_not_fetch_captions(
    guide_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("link must not run yt-dlp")

    monkeypatch.setattr("video_guide.captions.default_runner", boom)
    result = RUNNER.invoke(app, ["link", "https://youtu.be/abcdefghijk", "754"])
    assert result.exit_code == 0
    body = _payload(result)
    assert body["url"] == "https://www.youtube.com/watch?v=abcdefghijk&t=749s"
    assert body["timestamp"] == "12:34"
    assert body["playback_timestamp"] == "12:29"


def test_cli_reports_missing_ytdlp(guide_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("video_guide.captions.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "video_guide.captions._candidate_bin_dirs", lambda: [guide_home / "empty-bin"]
    )
    result = RUNNER.invoke(app, ["transcript", "get", URL])
    assert result.exit_code == 1
    body = _payload(result)
    assert body["error"] == "ytdlp_missing"
    assert "PATH" in str(body["message"])


def test_cli_profile_cookies_round_trip(guide_home: Path) -> None:
    refused = RUNNER.invoke(app, ["profile", "set", "--cookies-from-browser", "opera"])
    assert refused.exit_code == 1
    assert _payload(refused)["error"] == "invalid_profile"

    saved = RUNNER.invoke(app, ["profile", "set", "--cookies-from-browser", "Chrome"])
    assert saved.exit_code == 0
    shown = _payload(RUNNER.invoke(app, ["profile", "show"]))
    assert shown["cookies_from_browser"] == "chrome"
    assert shown["schema_version"] == 1

    cleared = RUNNER.invoke(app, ["profile", "set", "--clear-cookies"])
    assert cleared.exit_code == 0
    assert _payload(cleared)["cookies_from_browser"] is None


def test_cli_profile_cookies_file_round_trip(guide_home: Path, tmp_path: Path) -> None:
    secret = "NETSCAPE_SECRET_COOKIE_VALUE"
    cookies_path = tmp_path / "cookies.txt"
    cookies_path.write_text(f"# Netscape HTTP Cookie File\nSID\t{secret}\n", encoding="utf-8")
    refused = RUNNER.invoke(app, ["profile", "set", "--cookies", "--extract-audio"])
    assert refused.exit_code == 1
    assert _payload(refused)["error"] == "invalid_profile"
    assert secret not in refused.stdout

    saved = RUNNER.invoke(app, ["profile", "set", "--cookies", str(cookies_path)])
    assert saved.exit_code == 0
    assert secret not in saved.stdout
    shown = _payload(RUNNER.invoke(app, ["profile", "show"]))
    assert shown["cookies_file"] == str(cookies_path)

    cleared = RUNNER.invoke(app, ["profile", "set", "--clear-cookies-file"])
    assert cleared.exit_code == 0
    assert _payload(cleared)["cookies_file"] is None


def test_cli_profile_caption_language(guide_home: Path) -> None:
    refused = RUNNER.invoke(app, ["profile", "set", "--caption-language", "--extract-audio"])
    assert refused.exit_code == 1
    saved = RUNNER.invoke(app, ["profile", "set", "--caption-language", "zh-Hans"])
    assert saved.exit_code == 0
    assert _payload(saved)["preferred_caption_languages"] == ["zh-Hans"]
    cleared = RUNNER.invoke(app, ["profile", "set", "--clear-caption-language"])
    assert cleared.exit_code == 0
    assert _payload(cleared)["preferred_caption_languages"] == []


def test_cli_rejects_a_non_http_link(guide_home: Path) -> None:
    result = RUNNER.invoke(app, ["link", "javascript:alert(1)", "10"])
    assert result.exit_code == 1
    body = _payload(result)
    assert body["error"] == "bad_url"
    assert body["ok"] is False


def test_cli_help() -> None:
    result = RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "search" in result.stdout
    assert "link" in result.stdout
