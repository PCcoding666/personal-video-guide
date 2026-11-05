"""The skill package names real tools and ships the references it points at."""

from __future__ import annotations

import re
from pathlib import Path

from video_guide.mcp_server import TOOL_NAMES

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "personal-video-guide"
REFERENCES = (
    "references/watch-along.md",
    "references/evidence.md",
    "references/guide-page.md",
    "references/onboarding.md",
    "references/daily-digest.md",
    "hosts/workbuddy.md",
)


def test_skill_frontmatter_and_references() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match is not None
    frontmatter = match.group(1)
    assert re.search(r"^name:\s*personal-video-guide\s*$", frontmatter, re.MULTILINE)
    assert re.search(r"^description:\s*\S", frontmatter, re.MULTILINE)
    for relative in REFERENCES:
        assert (SKILL / relative).is_file(), relative
        assert relative in text
    mentioned = set(re.findall(r"`([a-z_]+)`", text))
    required = set(TOOL_NAMES)
    assert required <= mentioned
