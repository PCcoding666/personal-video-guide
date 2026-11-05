"""``video-guide`` command line for the watching loop.

``transcript get`` reports caption metadata, ``search`` finds a quote, and
``link`` builds the playback URL. Output is JSON on stdout. Browser cookies
stay off until ``profile set --cookies-from-browser`` names one.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Annotated, Any

import typer

from .service import GuideService
from .state import GuideStateError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Local video guide: captions, search, and playback links. No media download.",
)
transcript_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Fetch caption metadata, outline a video, or read a time window.",
)
profile_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Show or update the local profile. Cookies stay off unless you set a browser.",
)
app.add_typer(transcript_app, name="transcript")
app.add_typer(profile_app, name="profile")


def main() -> None:
    app()


def _service() -> GuideService:
    return GuideService()


def _emit(payload: Mapping[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload.get("ok", False):
        raise typer.Exit(code=1)


def _run(func: Callable[[], Mapping[str, Any]]) -> None:
    try:
        payload = func()
    except GuideStateError as exc:
        payload = {"ok": False, "error": "state", "message": str(exc)}
    except json.JSONDecodeError:
        payload = {"ok": False, "error": "state", "message": "A state file is not valid JSON."}
    _emit(payload)


@transcript_app.command("get")
def transcript_get(
    url: str = typer.Argument(..., help="YouTube video URL or 11-character video id"),
) -> None:
    """Fetch captions (or read the cache) and print language, kind, and cue count."""
    _run(lambda: _service().transcript_get(url))


@transcript_app.command("outline")
def transcript_outline(
    url: str = typer.Argument(..., help="YouTube video URL or 11-character video id"),
    description: str = typer.Option(
        "",
        "--description",
        help="Chapter lines such as '00:00 Intro'. Omit this to use chapters saved with the captions.",
    ),
) -> None:
    """Print chapters from metadata, or 5-minute windows when none were found."""
    _run(lambda: _service().transcript_outline(url, description))


@transcript_app.command("read")
def transcript_read(
    url: str = typer.Argument(..., help="YouTube video URL or 11-character video id"),
    start: float = typer.Argument(..., help="Window start, in seconds"),
    end: float = typer.Argument(..., help="Window end, in seconds"),
) -> None:
    """Print the cues overlapping a time window, up to 8,000 characters."""
    _run(lambda: _service().transcript_read(url, start, end))


@app.command("search")
def search_command(
    url: str = typer.Argument(..., help="YouTube video URL or 11-character video id"),
    phrase: str = typer.Argument(..., help="Quote or keywords to find"),
    extra: Annotated[
        list[str] | None,
        typer.Option(
            "--phrase",
            help="Another phrasing of the same question. Scores are summed.",
        ),
    ] = None,
) -> None:
    """Search captions and print matching cues with tool-built playback links."""
    phrases = [phrase, *(extra or [])]
    _run(lambda: _service().search(url, phrases))


@app.command("link")
def link_command(
    url: str = typer.Argument(..., help="YouTube video URL or 11-character video id"),
    seconds: float = typer.Argument(..., help="Quote time in seconds. Playback starts 5s earlier."),
) -> None:
    """Print a YouTube URL whose t= starts 5 seconds before the given moment."""
    _run(lambda: _service().link(url, seconds))


@profile_app.command("show")
def profile_show() -> None:
    """Print the stored profile, or the defaults when none has been written."""
    _run(lambda: _service().show_profile())


@profile_app.command("set")
def profile_set(
    cookies_from_browser: str | None = typer.Option(
        None,
        "--cookies-from-browser",
        help="Browser yt-dlp may read cookies from: chrome, firefox, safari, edge, brave.",
    ),
    ytdlp_path: str | None = typer.Option(
        None,
        "--ytdlp-path",
        help="Path to a yt-dlp executable. Default is to look on PATH.",
    ),
    clear_cookies: bool = typer.Option(
        False,
        "--clear-cookies",
        help="Turn browser cookies back off.",
    ),
    cookies: str | None = typer.Option(
        None,
        "--cookies",
        help="Path to a Netscape cookies.txt file. The file is not read or logged.",
    ),
    clear_cookies_file: bool = typer.Option(
        False,
        "--clear-cookies-file",
        help="Forget the cookies file path.",
    ),
    caption_language: str | None = typer.Option(
        None,
        "--caption-language",
        help="Extra caption language, e.g. en or zh-Hans. At most one extra track is downloaded.",
    ),
    clear_caption_language: bool = typer.Option(
        False,
        "--clear-caption-language",
        help="Download only the original-language captions.",
    ),
) -> None:
    """Update caption settings. Cookie contents are never read or stored."""
    if clear_cookies and cookies_from_browser is not None:
        _emit(
            {
                "ok": False,
                "error": "invalid_profile",
                "message": "Pass either --cookies-from-browser or --clear-cookies.",
            }
        )
        return
    if clear_cookies_file and cookies is not None:
        _emit(
            {
                "ok": False,
                "error": "invalid_profile",
                "message": "Pass either --cookies or --clear-cookies-file.",
            }
        )
        return
    if clear_caption_language and caption_language is not None:
        _emit(
            {
                "ok": False,
                "error": "invalid_profile",
                "message": "Pass either --caption-language or --clear-caption-language.",
            }
        )
        return

    def apply() -> Mapping[str, Any]:
        return _service().update_profile(
            cookies_from_browser=None if clear_cookies else cookies_from_browser,
            set_cookies=clear_cookies or cookies_from_browser is not None,
            cookies_file=None if clear_cookies_file else cookies,
            set_cookies_file=clear_cookies_file or cookies is not None,
            caption_language=None if clear_caption_language else caption_language,
            set_caption_language=clear_caption_language or caption_language is not None,
            ytdlp_path=ytdlp_path,
            set_ytdlp_path=ytdlp_path is not None,
        )

    _run(apply)


@app.command("mcp")
def mcp_command() -> None:
    """Serve the guide tools on stdin/stdout for a local host such as Codex."""
    from .mcp_server import McpExtraMissing, serve_stdio

    try:
        serve_stdio()
    except McpExtraMissing as exc:
        _emit({"ok": False, "error": "mcp_missing", "message": str(exc)})


if __name__ == "__main__":
    main()
