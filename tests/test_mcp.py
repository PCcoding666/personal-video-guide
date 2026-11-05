"""Offline tests for the stdio MCP server. No network and no real yt-dlp."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from video_guide.cli import app
from video_guide.mcp_server import TOOL_NAMES, McpExtraMissing, build_server
from video_guide.service import GuideService
from video_guide.state import GuideStore

RUNNER = CliRunner()


def test_registered_tools_match_the_cli_names(tmp_path: Path) -> None:
    server = build_server(GuideService(GuideStore(tmp_path)))
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == set(TOOL_NAMES)
    assert "url" in by_name["transcript_get"].inputSchema["properties"]
    assert "phrases" in by_name["search"].inputSchema["properties"]
    assert "seconds" in by_name["link"].inputSchema["properties"]
    profile = by_name["profile_set"].inputSchema["properties"]
    assert "cookies" in profile
    assert "cookies_from_browser" in profile
    assert "caption_language" in profile
    assert "ytdlp_path" in profile
    guide = by_name["open_guide"].inputSchema["properties"]
    assert "segments" in guide
    assert "why" in guide


def test_mcp_command_explains_a_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = {
        key: sys.modules[key] for key in list(sys.modules) if key == "mcp" or key.startswith("mcp.")
    }
    for key in saved:
        del sys.modules[key]
    monkeypatch.setitem(sys.modules, "mcp", None)
    try:
        result = RUNNER.invoke(app, ["mcp"])
    finally:
        sys.modules.pop("mcp", None)
        sys.modules.update(saved)
    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert payload["error"] == "mcp_missing"
    assert "personal-video-guide[mcp]" in payload["message"]


def test_stdio_server_lists_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDSNAP_GUIDE_HOME", str(tmp_path))
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def listed() -> list[str]:
        env = dict(os.environ)
        env["VIDSNAP_GUIDE_HOME"] = str(tmp_path)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "video_guide", "mcp"],
            env=env,
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            return [tool.name for tool in result.tools]

    names = asyncio.run(asyncio.wait_for(listed(), timeout=20))
    assert set(names) == set(TOOL_NAMES)


def test_load_failure_is_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = {
        key: sys.modules[key] for key in list(sys.modules) if key == "mcp" or key.startswith("mcp.")
    }
    for key in saved:
        del sys.modules[key]
    monkeypatch.setitem(sys.modules, "mcp", None)
    try:
        with pytest.raises(McpExtraMissing):
            build_server()
    finally:
        sys.modules.pop("mcp", None)
        sys.modules.update(saved)
