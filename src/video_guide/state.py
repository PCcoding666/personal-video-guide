"""Local, append-only state for the personal video guide.

State lives outside the repository under ``~/.video-guide/`` by default, or
the path named by ``VIDSNAP_GUIDE_HOME``. Everything is plain, hand-editable
text. The profile is written atomically (temp file, then ``os.replace``) so an
interrupted write never corrupts the previous copy; the seen and feedback logs
are append-only JSONL with one record per line. A clock is injected by the
caller so tests stay deterministic.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .models import SCHEMA_VERSION, FeedbackEntry, FeedbackKind, Profile, SeenVideo

_ENV_HOME = "VIDSNAP_GUIDE_HOME"
_DEFAULT_HOME = ".video-guide"


class GuideStateError(Exception):
    """Local guide state is missing, unreadable, or from an unsupported version."""


def resolve_home(explicit: Path | None = None) -> Path:
    """Resolve the state directory: explicit argument, then env var, then default."""
    if explicit is not None:
        return explicit
    from_env = os.environ.get(_ENV_HOME)
    if from_env:
        return Path(from_env)
    return Path.home() / _DEFAULT_HOME


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, leaving the old file intact on error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _append_jsonl(path: Path, line: str) -> None:
    """Append one JSON record as a single line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _check_version(raw: object, source: str) -> None:
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        raise GuideStateError(f"unsupported schema_version in {source}: {version!r}")


class GuideStore:
    """Read and write the guide's local state files under one home directory."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = resolve_home(home)

    @property
    def profile_path(self) -> Path:
        return self.home / "profile.json"

    @property
    def seen_path(self) -> Path:
        return self.home / "seen.jsonl"

    @property
    def feedback_path(self) -> Path:
        return self.home / "feedback.jsonl"

    def profile(self) -> Profile:
        """Return the stored profile, or defaults when none has been written yet."""
        if not self.profile_path.exists():
            return Profile()
        raw = json.loads(self.profile_path.read_text(encoding="utf-8"))
        _check_version(raw, "profile.json")
        return Profile.model_validate(raw)

    def set_profile(self, profile: Profile) -> None:
        _atomic_write_text(self.profile_path, profile.model_dump_json(indent=2) + "\n")

    def mark_seen(self, video_id: str, *, now: datetime) -> None:
        record = SeenVideo(video_id=video_id, seen_at=now)
        _append_jsonl(self.seen_path, record.model_dump_json())

    def is_seen(self, video_id: str) -> bool:
        return video_id in self.seen_ids()

    def seen_ids(self) -> set[str]:
        ids: set[str] = set()
        if not self.seen_path.exists():
            return ids
        for line in self.seen_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            raw = json.loads(stripped)
            _check_version(raw, "seen.jsonl")
            ids.add(SeenVideo.model_validate(raw).video_id)
        return ids

    def add_feedback(
        self, video_id: str, kind: FeedbackKind, *, now: datetime, note: str | None = None
    ) -> None:
        entry = FeedbackEntry(video_id=video_id, kind=kind, note=note, created_at=now)
        _append_jsonl(self.feedback_path, entry.model_dump_json())
