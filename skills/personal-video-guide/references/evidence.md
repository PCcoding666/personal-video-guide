# Evidence

The host model writes the reason. The toolkit writes the time and the link. Those jobs stay separate.

## Allowed

- A quote whose characters come from `transcript_read` or `search` (`text`, or the context fields when you need the neighboring cue).
- A timestamp that is the `timestamp` field on that same result, or the `start` you passed to `link`.
- A URL that is the `url` field on a tool result. `link` is the tool that builds one. Playback starts 5 seconds before the moment you pass, and never before zero. `playback_timestamp` is the clock for `t=`. `timestamp` is the moment you asked about. Show the user the moment they asked about, and give them the tool URL.

## Not allowed

- A quote when `transcript_get` returned `ok: false`. Say which error (`no_captions`, `bot_check`, `rate_limited`, `cookies_unreadable`, `ytdlp_missing`) and the hint. Stop.
- A timestamp you computed in your head, including "about halfway" turned into a number.
- A YouTube URL you assembled, even if you remember the `t=` rule.
- A paraphrase inside quotation marks. Paraphrase in the reason line, without quotes.
- A claim that a chapter exists when `source` is `window`.

## Card shape

```
12:04–14:10  Hiring
Why: You asked where they warn against hiring too early, and this window is inside your 15-minute budget.
Evidence: "we hired before we knew the job"
Link: https://www.youtube.com/watch?v=VIDEO_ID&t=719s
```

The link line is copied from the tool. The evidence line is copied from the cue. The why line is yours.

If several hits compete, show the one you recommend and name the others by their tool timestamps so the user can reject your pick.
