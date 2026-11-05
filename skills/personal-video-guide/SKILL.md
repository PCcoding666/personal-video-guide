---
name: personal-video-guide
description: Guide the user through watching a YouTube video and recommend where to jump. Use when they share a YouTube link, ask what is worth their time, or want a watch-along. Outline first, search captions, and open a local guide page. Never invent quotes or timestamps.
---

# Personal video guide

You guide watching. You do not summarize the video in place of watching it.

The toolkit is local. It does not call a model. You do the reasoning. It owns captions, times, and links.

## When to use this

Use it for a YouTube URL or an 11-character video id, a question about something the user half remembers, or a request to pick what to watch given a time budget.

Daily discovery is a different mode. See [references/onboarding.md](references/onboarding.md) and [references/daily-digest.md](references/daily-digest.md). This skill's main path is watch-along.

## Tools

Call these MCP tools. The same actions exist on the `video-guide` CLI.

| Tool | Use |
| --- | --- |
| `transcript_get` | Fetch or reuse captions. Read `language` and `kind`. |
| `transcript_outline` | Chapters, or 5-minute windows. Call this before choosing segments. |
| `transcript_read` | Cues between two seconds, up to 8,000 characters. |
| `search` | Lexical search. Pass several English phrasings in `phrases`. |
| `link` | The only way to build a playback URL. |
| `profile_get` | Read caption language, cookies settings, and yt-dlp path. |
| `profile_set` | Set `cookies`, `cookies_from_browser`, `caption_language`, or `ytdlp_path`. |
| `open_guide` | Write the local guide page and open it in the browser. |

Details: [references/watch-along.md](references/watch-along.md), [references/evidence.md](references/evidence.md), [references/guide-page.md](references/guide-page.md).

## Watch-along, short version

1. `transcript_get` on the URL. If `ok` is false, say so and stop. Do not guess quotes or times. Offer the profile fix from the error hint (`cookies` file, caption language, yt-dlp).
2. `transcript_outline` with no description unless the user pasted chapters. The tool uses chapters saved from the video metadata.
3. From the user's goal and time budget, pick a few sections. `transcript_read` those windows.
4. When the user asks a fuzzy question, rewrite it into several plain English phrasings and pass them all to `search`.
5. Every playback link in your reply comes from `link` or from a URL the tools already returned. Do not type `t=` yourself.
6. `open_guide` with `why`, the segments you chose, quotes copied from tool output, and any timestamped answers. Leave `open_browser` true unless the user asked not to open a browser.

## Recommendation card

One card per segment, in watch order:

```
mm:ss–mm:ss  Title
Why: one sentence tied to the user's goal.
Evidence: "exact cue text" (from transcript_read or search)
Link: the url field from that tool
```

Then tell the user the guide page is open and that Play all walks the segments in order.

## Evidence

No caption track means no quote and no timestamp. A quote is copied from tool output, not paraphrased into quotation marks. Full rules: [references/evidence.md](references/evidence.md).

## Host notes

Codex launches `video-guide mcp` from the plugin. WorkBuddy setup is in [hosts/workbuddy.md](hosts/workbuddy.md). You create any schedule in the host, after the user agrees. This package does not schedule itself.
