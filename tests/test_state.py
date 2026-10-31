"""Offline tests for the personal video guide's local state store."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from video_guide.models import FeedbackKind, Profile
from video_guide.state import GuideStateError, GuideStore, resolve_home

FIXED = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)


def test_home_is_created_on_first_write(tmp_path: Path) -> None:
    home = tmp_path / "guide-home"
    assert not home.exists()
    GuideStore(home).set_profile(Profile(interests=("ai",)))
    assert home.is_dir()


def test_env_var_overrides_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("VIDSNAP_GUIDE_HOME", str(target))
    assert resolve_home() == target
    explicit = tmp_path / "explicit"
    assert resolve_home(explicit) == explicit


def test_profile_round_trips(tmp_path: Path) -> None:
    store = GuideStore(tmp_path)
    profile = Profile(interests=("ai", "startups"), max_cards=5, watch_budget_minutes=45)
    store.set_profile(profile)
    assert store.profile() == profile


def test_profile_defaults_when_absent(tmp_path: Path) -> None:
    assert GuideStore(tmp_path).profile() == Profile()


def test_seen_append_then_is_seen(tmp_path: Path) -> None:
    store = GuideStore(tmp_path)
    assert store.is_seen("vid-1") is False
    store.mark_seen("vid-1", now=FIXED)
    assert store.is_seen("vid-1") is True
    assert store.is_seen("vid-2") is False


def test_atomic_write_keeps_old_profile_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = GuideStore(tmp_path)
    first = Profile(interests=("ai",))
    store.set_profile(first)

    import video_guide.state as state_module

    def boom(src: object, dst: object) -> None:
        raise RuntimeError("replace failed")

    monkeypatch.setattr(state_module.os, "replace", boom)
    with pytest.raises(RuntimeError):
        store.set_profile(Profile(interests=("changed",)))

    assert store.profile() == first
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    store = GuideStore(tmp_path)
    store.set_profile(Profile())
    raw = json.loads(store.profile_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 99
    store.profile_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(GuideStateError):
        store.profile()


def test_feedback_only_accepts_known_kinds(tmp_path: Path) -> None:
    store = GuideStore(tmp_path)
    store.add_feedback("vid-1", FeedbackKind.INTERESTED, now=FIXED)
    store.add_feedback("vid-1", FeedbackKind.WATCHED, now=FIXED, note="finished it")
    with pytest.raises(ValueError):
        FeedbackKind("maybe")
