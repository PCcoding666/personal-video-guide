# Personal video guide: host compatibility

Status of each host capability the [personal video guide design](superpowers/specs/2026-10-31-personal-video-guide-design.md) relies on. Values are `supported`, `unsupported`, or `unverified`; a capability is `supported` only after a recorded check. Raw inputs, outputs, and account data stay outside the repository.

## WorkBuddy AI 5.7.6 (macOS), checked 2026-11-01

| # | Capability | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Timed YouTube captions without guide tools | supported, with caveats | Chat test on Lenny's Podcast's latest full episode (85.8 min): the agent found and used a locally installed `yt-dlp`, hit the proxy (502) and the YouTube bot check, then on its own retried with `--cookies-from-browser chrome` and fetched auto captions as json3 covering 0–5146 s of 5147 s. It noticed that the `en` track was machine-translated and switched to `en-orig`. Took about 10 minutes. Of the links it returned, one pointed 37 s away from the time it displayed (39:06 shown, `t=2383s` linked) and one had Chinese text URL-encoded into the URL. On a machine without `yt-dlp` or a signed-in browser this path would not exist. |
| 2a | Run local commands from chat | supported | Chat test: the agent ran shell commands and found a locally installed `yt-dlp` without being asked to install anything. |
| 2b | Local stdio MCP server | supported | A throwaway stdio server added to `~/.workbuddy-ai/mcp.json` (`command` + `args`) appeared under Connectors → Custom connectors after an app restart, required a one-time **Trust** click, then listed both tools. In a new chat the agent called `probe_ping` and returned the tool's raw output. The config file is also editable from the in-app MCP editor. |
| 2c | Skills | supported (format) | Skills live in `~/.workbuddy/skills/<name>/SKILL.md` and `~/.workbuddy-ai/skills/<name>/SKILL.md` with `name` and `description` frontmatter; a scheduled task and the chat composer can both attach Skills and Connectors. Not yet exercised with the guide Skill. |
| 3a | Scheduled tasks | supported | Scheduled page: name, prompt, workspace, permission mode, model, and repeat (Interval, Once, Daily, Weekly, Every 2 weeks, Monthly, Yearly) with a time and a validity window. |
| 3b | Agent creates and runs a schedule from chat | supported | Asked in chat to create a one-off task at 02:05 that calls `probe_ping`; the agent created it itself (no form) and reported name, time and status. At 02:05 the run started on time, called the local MCP tool, and posted the tool's raw output in the task's conversation. The test task was deleted afterwards. |
| 3c | Runs while the app is closed | unsupported | Run history of an existing weekly task shows `runKind: missed` runs that fire when the app next opens; one missed run recorded no result. Daily digests need the app running (and the Mac awake) at the scheduled time; otherwise they arrive late, once, when the app next opens. |
| 3d | Delivery outside the app | unverified | Task records carry `push_to_wechat` and `push_to_wecom_bot` flags; not seen in the create form or exercised. |
| 4 | Long-term memory | unverified | Global `MEMORY.md` / `USER.md` and per-workspace `.workbuddy-ai/memory/<date>.md` files exist; whether a Skill can rely on them is untested. |
| 5 | Current browser page URL or playback position | unverified | An `agent-browser` plugin is enabled; untested. |

## Caption fetching from this machine (no host)

Checked 2026-11-01 from the development Mac, with traffic going through a local proxy whose exit is a data-center IP. Six target channels: Y Combinator, Lenny's Podcast, Dwarkesh Patel, a16z, No Priors, Latent Space.

| Step | Result |
| --- | --- |
| Resolve `@handle` to channel ID (channel page) | 6/6, but slow: 9–121 s per channel through the proxy. Resolve once when a source is added and store the ID. |
| Channel RSS (`/feeds/videos.xml?channel_id=`) | 6/6; 15 latest entries each. |
| Description in RSS | Present in 15/15 entries for five channels; Dwarkesh Patel only 3/15. |
| Chapter timestamps in description (≥3 lines) | 2–12 of 15 entries per channel; chapters cannot be the only outline source. |
| Captions via `youtube-transcript-api` (no cookies) | 0/10: `RequestBlocked`. |
| Captions via `yt-dlp --skip-download` 2026.10.26 (no cookies) | 0/10: "Sign in to confirm you're not a bot". |

Without cookies, YouTube refuses caption requests from this exit IP, so feed discovery works but caption fetching needs user-supplied cookies or a residential exit.

## What this means for v1

- **Discovery** can rely on RSS: it worked for every target channel without cookies. Resolve each channel ID once at `sources.add` time; the channel page is the slow part.
- **Captions** are the hard part. From a data-center exit IP every unauthenticated request was refused. What worked was `yt-dlp` with the user's own signed-in browser cookies. The tool package needs an explicit, user-enabled cookie source and must prefer the `en-orig` track over the machine-translated `en` track for auto captions.
- **Links** must come from the tool: the host model, left to itself, produced a link 37 s off from its own label and a link with text URL-encoded into it.
- **WorkBuddy integration** works end to end: Skill folder, local stdio MCP (after a one-time Trust), a schedule the agent creates from chat, and scheduled runs that call MCP tools. Its limits: the app must be running at the scheduled time, and pushing digests outside the app (WeChat) is still unverified.
- **Not yet checked:** long-term memory (4), browser page context (5), out-of-app delivery (3d), and a second host.

Test artefacts created during these checks (a throwaway MCP entry and a one-off scheduled task) were removed afterwards. The three chat conversations remain in WorkBuddy's history.
