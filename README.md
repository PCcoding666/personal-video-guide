# Personal Video Guide

**别让 AI 总结视频了，让它带你看。**

A local toolkit that lets a host agent (Claude, ChatGPT, WorkBuddy, …) act as your
personal video guide: it finds the new episodes from the channels you care about,
tells you *why* each one is worth your time, and hands you a link that starts
playing at the exact second. The agent does the thinking; this toolkit does the
deterministic parts — discovery, captions, search, timestamps, links — so the
agent never invents a quote or a time.

The method comes from 刘小排's post《别让AI总结视频了，让它带你看》(2026-10-27).
This repository is an open implementation of it.

## The four steps

| Step | What you do | What the guide does |
| --- | --- | --- |
| **1. Align** | Tell the agent what you care about and which channels to watch. | Stores your profile and sources; resolves channel links to feeds. The host sets up the daily schedule — after you confirm. |
| **2. Daily digest** | Read the morning card; pick what to watch yourself. | Checks every feed, finds what is new, locates the best segments, and records what it recommended so it never pushes the same video twice. |
| **3. Jump in** | While watching, ask about a half-remembered point. | Searches the captions for the actual words and returns a link with `t=` set a few seconds before them. |
| **4. What's next** | Ask what else in this episode is worth it, given what you are thinking about. | Serves the transcript outline and windows so the host can recommend from your profile and the conversation. |

The guide never summarizes a video for you and never replaces watching. If it
cannot get the original words, it says so instead of making them up.

## How it fits together

```
┌──────────────┐   calls tools    ┌───────────────────────┐   HTTPS GET only
│  Host agent  │ ───────────────▶ │  video_guide          │ ────────────────▶ YouTube feeds
│  (model,     │ ◀─────────────── │  CLI + stdio MCP      │                   & caption tracks
│  schedule,   │   facts, links   │  no model calls       │
│  memory)     │                  │  local state only     │
└──────────────┘                  └───────────────────────┘
```

- **Skill** (`skills/personal-video-guide/`) — a short `SKILL.md` that teaches
  the host the method: alignment questions, the digest card format, the evidence
  rule, and what to do when something is missing.
- **Toolkit** (`src/video_guide/`) — the deterministic half. Source
  normalization, RSS discovery with de-duplication, caption fetch and cache,
  outline / read / lexical search over transcripts, link generation, and an
  atomic local state store (`~/.video-guide/`).
- **Host** — your agent. It judges relevance, writes the "why it's worth it",
  rewrites your fuzzy question into the video's language, runs the schedule,
  and delivers the card.

Full design: [docs/design.md](docs/design.md). Build plan:
[docs/plan.md](docs/plan.md). What each host can actually do today:
[docs/host-compatibility.md](docs/host-compatibility.md).

## Status

Early. The state store and feed discovery are implemented and tested offline;
caption fetching, transcript search, the CLI/MCP surface and the Skill are next.
See [docs/plan.md](docs/plan.md) for the task list and
[docs/host-compatibility.md](docs/host-compatibility.md) for what has been
verified against a real host.

| Area | State |
| --- | --- |
| Profile, sources, seen / feedback logs (atomic writes, schema-versioned) | done |
| Channel link → feed; `@handle` resolution; RSS discovery with 48 h overlap and per-source isolation | done |
| Caption fetch and cache | planned |
| Transcript outline, read, lexical search, `link.at` | planned |
| CLI + stdio MCP server | planned |
| `SKILL.md` and host install notes | planned |

## Development

Python 3.10+. No network, no model calls, no real media in tests.

    python -m pip install -e '.[dev]'
    python -m pytest -q
    python -m mypy src
    ruff check . && ruff format --check src tests

## Boundaries

Deliberately out of scope: accounts, SaaS, a player, media (video/audio)
downloads, anti-bot bypasses, bundled cookies, vector search, schedulers or
notification senders inside the toolkit. Scheduling and delivery belong to the
host. Network access is an allow-list of HTTPS GET requests (feed and caption
endpoints, plus a one-time channel-page fetch to resolve a handle). Any cookies
are supplied by you locally and are never stored in the repository, logs, or
the state directory.

Originally prototyped inside [VidSnap](https://github.com/PCcoding666/Vidsnap)
on the `feat/personal-video-guide` branch and split out on 2026-11-01.

## License

MIT
