# Repository Guidelines

Instructions for any coding agent working in this repository. Follow them exactly. If a requested change conflicts with this document, surface the conflict instead of proceeding silently.

## What this is

`video_guide` is a local, single-user tool package (CLI plus stdio MCP server) behind the cross-host `personal-video-guide` Skill. It discovers new videos from user-added feeds, fetches caption tracks, and outlines, reads, searches, and links transcript windows. It runs only when a host agent or the user calls it, never schedules itself or sends notifications, and makes no model calls. The host agent owns reasoning, scheduling, delivery, memory and browser context; this package owns everything that must be deterministic — de-duplication, timestamps and links.

Design: `docs/design.md`. Plan: `docs/plan.md`. Host findings: `docs/host-compatibility.md`.

## Layout

- `src/video_guide/models.py` — pydantic models and `SCHEMA_VERSION`.
- `src/video_guide/state.py` — `GuideStore`: atomic writes, JSONL logs, caption cache, schema guard, injected clock. State lives outside the repository (default `~/.video-guide/`, configurable).
- `src/video_guide/fetch.py` — allow-listed HTTPS GET with a size cap. YouTube captions do not go through this client.
- `src/video_guide/sources.py` — channel / feed link normalization, `@handle` resolution.
- `src/video_guide/feeds.py` — Atom/RSS parsing, de-duplication, 48 h overlap, per-source failure isolation, Podcasting 2.0 transcript URLs.
- `src/video_guide/captions.py` — VTT/SRT/json3 parsing, yt-dlp subtitle fetch, caption cache, podcast transcript fetch.
- `src/video_guide/transcript.py` — chapter outline or 5-minute windows, and a bounded time-window read.
- `src/video_guide/search.py` — BM25 lexical search over cues.
- `src/video_guide/links.py` — `link.at` playback URLs and `h:m:s` clocks.
- `src/video_guide/service.py` — shared entry point for the CLI and the MCP server.
- `src/video_guide/cli.py` — `video-guide` typer app, including `video-guide mcp`.
- `src/video_guide/mcp_server.py` — stdio MCP server. Tool names match the CLI with underscores.
- `src/video_guide/guide_page.py` — local watch-along page served on 127.0.0.1.
- `skills/personal-video-guide/` — Codex skill: watch-along method, evidence rules, guide page.
- `tests/` — offline tests; network via `httpx.MockTransport`, yt-dlp via an injected runner, clock injected.

## Network and data boundaries

- Network access is limited to HTTPS GET requests against an allow-list: YouTube feed and caption endpoints, a one-time fetch of a user-supplied YouTube channel page solely to resolve its feed channel id, and feed URLs the user explicitly added.
- One exception to the HTTPS GET rule: YouTube captions are fetched by invoking a `yt-dlp` executable the user installed themselves (a profile path, then `PATH`, then the unresolved interpreter directory, the sysconfig scripts directory, and `sys.prefix/bin`, before the symlink-resolved interpreter directory). The package never installs, bundles, or pins `yt-dlp`. It reads one `-J` metadata dump, then downloads one caption language: the profile caption language when that track exists, otherwise the spoken-language track inferred from the title's script (a Latin title uses `en` or `en-orig`, not the first `*-orig` auto-dub). A script mismatch allows one alternate download. The allow-listed arguments are `-J`, `--skip-download`, `--ignore-no-formats-error`, `--write-subs`, `--write-auto-subs`, `--sub-langs`, `--sub-format`, `--no-playlist`, and `-o` into a temporary directory. It adds `--cookies-from-browser <browser>` only when the profile names a browser, or `--cookies <path>` only when the profile names a Netscape cookies.txt path. When both are set, only `--cookies` is passed. It never passes flags that download media.
- Never download media (video or audio). Never bundle anti-bot bypasses or cookies. Any cookies the user supplies locally are never read by the package — not in the repository, logs, caches, or the state directory. The profile may store a browser name or a cookies file path; caption errors and logs omit that path, and the file itself is never opened. The optional `impersonate` extra installs `curl-cffi` for a yt-dlp that shares this environment. The package does not turn impersonation on itself.
- Never commit, publish, or embed in fixtures: credentials, cookies, `.env` files, media, real caption tracks, user state, transcript caches, or raw provider requests/responses.
- Not in scope: accounts, SaaS, databases, queues, schedulers, notification senders, model calls, vector search. A local watch-along page may embed YouTube's iframe from `127.0.0.1`. The package does not bundle a player and does not download media. That page is an explicit product decision for the Codex plugin. Other scope changes still need an explicit decision.

## Tests and gates

- Add or update tests before changing behavior.
- Tests are offline and deterministic: no network, no model calls, no real media. Test data is hand-written in the test code.
- Keep changes minimal and scoped to the task; no drive-by refactors.
- Every change must pass:
  - `ruff format --check src tests`
  - `ruff check .`
  - `python -m mypy src`
  - `python -m pytest -q`
  - `git diff --check`
- Never weaken or bypass a gate to make a change pass.
