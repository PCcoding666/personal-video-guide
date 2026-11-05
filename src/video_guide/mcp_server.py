"""stdio MCP server for a local host such as Codex.

Tool names match the CLI with spaces replaced by underscores. The ``mcp``
package is imported only when the server is built, so ``video-guide`` still
runs when that extra is not installed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .service import GuideService

TOOL_NAMES: tuple[str, ...] = (
    "transcript_get",
    "transcript_outline",
    "transcript_read",
    "search",
    "link",
    "profile_get",
    "profile_set",
    "open_guide",
)


class McpExtraMissing(RuntimeError):
    """Raised when ``video-guide mcp`` is started without the mcp extra."""


class GuideSegment(BaseModel):
    """One recommended range. Quotes must be caption text the host already retrieved."""

    model_config = ConfigDict(extra="ignore")

    start: float
    end: float
    title: str = ""
    reason: str = ""
    quotes: list[str] = Field(default_factory=list)


class GuideAnswer(BaseModel):
    """A timestamped answer the host wrote from caption evidence."""

    model_config = ConfigDict(extra="ignore")

    seconds: float
    question: str = ""
    text: str = ""


_F = TypeVar("_F", bound=Callable[..., Any])


def build_server(service: GuideService | None = None) -> Any:
    """Register the guide tools. Imports the MCP SDK on first use."""
    mcp = _load_fastmcp()("video-guide")
    guide = service if service is not None else GuideService()

    @_register(mcp, "transcript_get")
    def transcript_get(url: str) -> str:
        """Fetch captions or read the cache. Returns language, kind, duration, and cue count."""
        return _json(guide.transcript_get(url))

    @_register(mcp, "transcript_outline")
    def transcript_outline(url: str, description: str = "") -> str:
        """Outline the video. Uses yt-dlp chapters or description when description is empty."""
        return _json(guide.transcript_outline(url, description))

    @_register(mcp, "transcript_read")
    def transcript_read(url: str, start: float, end: float) -> str:
        """Read caption cues between start and end seconds, up to 8,000 characters."""
        return _json(guide.transcript_read(url, start, end))

    @_register(mcp, "search")
    def search(url: str, phrases: list[str]) -> str:
        """Search captions. Pass several phrasings of the same question. Links come from the tool."""
        return _json(guide.search(url, phrases))

    @_register(mcp, "link")
    def link(url: str, seconds: float) -> str:
        """Build a YouTube URL that starts 5 seconds before the given moment."""
        return _json(guide.link(url, seconds))

    @_register(mcp, "profile_get")
    def profile_get() -> str:
        """Show the local profile. Cookie file contents are never included."""
        return _json(guide.show_profile())

    @_register(mcp, "profile_set")
    def profile_set(
        cookies_from_browser: str | None = None,
        clear_cookies: bool = False,
        cookies: str | None = None,
        clear_cookies_file: bool = False,
        caption_language: str | None = None,
        clear_caption_language: bool = False,
        ytdlp_path: str | None = None,
    ) -> str:
        """Update caption settings. Matches video-guide profile set. Does not read cookie files."""
        if clear_cookies and cookies_from_browser is not None:
            return _json(
                {
                    "ok": False,
                    "error": "invalid_profile",
                    "message": "Pass either cookies_from_browser or clear_cookies.",
                }
            )
        if clear_cookies_file and cookies is not None:
            return _json(
                {
                    "ok": False,
                    "error": "invalid_profile",
                    "message": "Pass either cookies or clear_cookies_file.",
                }
            )
        if clear_caption_language and caption_language is not None:
            return _json(
                {
                    "ok": False,
                    "error": "invalid_profile",
                    "message": "Pass either caption_language or clear_caption_language.",
                }
            )
        return _json(
            guide.update_profile(
                cookies_from_browser=None if clear_cookies else cookies_from_browser,
                set_cookies=clear_cookies or cookies_from_browser is not None,
                cookies_file=None if clear_cookies_file else cookies,
                set_cookies_file=clear_cookies_file or cookies is not None,
                caption_language=None if clear_caption_language else caption_language,
                set_caption_language=clear_caption_language or caption_language is not None,
                ytdlp_path=ytdlp_path,
                set_ytdlp_path=ytdlp_path is not None,
            )
        )

    @_register(mcp, "open_guide")
    def open_guide(
        url: str,
        why: str,
        segments: list[GuideSegment],
        answers: list[GuideAnswer] | None = None,
        open_browser: bool = True,
    ) -> str:
        """Write a local guide page and serve it on 127.0.0.1. Does not download media."""
        payload = guide.open_guide(
            url,
            why=why,
            segments=[segment.model_dump() for segment in segments],
            answers=None if answers is None else [answer.model_dump() for answer in answers],
            open_browser=open_browser,
        )
        return _json(payload)

    return mcp


def serve_stdio(service: GuideService | None = None) -> None:
    """Run until the host closes stdin."""
    build_server(service).run(transport="stdio")


def _register(mcp: Any, name: str) -> Callable[[_F], _F]:
    """Attach a tool without exposing the SDK's untyped decorator to mypy."""

    def decorate(fn: _F) -> _F:
        mcp.tool(name=name, structured_output=False)(fn)
        return fn

    return decorate


def _load_fastmcp() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise McpExtraMissing(
            "The MCP extra is not installed. "
            "Install it with: python -m pip install 'personal-video-guide[mcp]'"
        ) from exc
    return FastMCP


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
