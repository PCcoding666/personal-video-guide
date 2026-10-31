# Repository Guidelines

Instructions for any coding agent working in this repository. Follow them exactly. If a requested change conflicts with this document, surface the conflict instead of proceeding silently.

## What this is

`video_guide` is a local, single-user tool package (CLI plus stdio MCP server) behind the cross-host `personal-video-guide` Skill. It discovers new videos from user-added feeds, fetches caption tracks, and outlines, reads, searches, and links transcript windows. It runs only when a host agent or the user calls it, never schedules itself or sends notifications, and makes no model calls. The host agent owns reasoning, scheduling, delivery, memory and browser context; this package owns everything that must be deterministic — de-duplication, timestamps and links.

Design: `docs/design.md`. Plan: `docs/plan.md`. Host findings: `docs/host-compatibility.md`.

## Layout

- `src/video_guide/models.py` — pydantic models and `SCHEMA_VERSION`.
- `src/video_guide/state.py` — `GuideStore`: atomic writes, JSONL logs, schema guard, injected clock. State lives outside the repository (default `~/.video-guide/`, configurable).
- `src/video_guide/fetch.py` — the only network path: allow-listed HTTPS GET with a size cap.
- `src/video_guide/sources.py` — channel / feed link normalization, `@handle` resolution.
- `src/video_guide/feeds.py` — Atom/RSS parsing, de-duplication, 48 h overlap, per-source failure isolation.
- `tests/` — offline tests; network via `httpx.MockTransport`, clock injected.

## Network and data boundaries

- Network access is limited to HTTPS GET requests against an allow-list: YouTube feed and caption endpoints, a one-time fetch of a user-supplied YouTube channel page solely to resolve its feed channel id, and feed URLs the user explicitly added.
- Never download media (video or audio). Never bundle anti-bot bypasses or cookies. Any cookies the user supplies locally are never stored in the repository, logs, or the state directory.
- Never commit, publish, or embed in fixtures: credentials, cookies, `.env` files, media, real caption tracks, user state, transcript caches, or raw provider requests/responses.
- Not in scope: accounts, SaaS, databases, queues, players, schedulers, notification senders, model calls, vector search. Changes outside scope require an explicit, separate decision.

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
