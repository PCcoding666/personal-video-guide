# Guide page

`open_guide` writes a JSON file under the state directory (`guides/<video_id>.json`) and serves a static page on `127.0.0.1`. YouTube's iframe does not run from `file://`, so the page is always served over HTTP. Nothing is downloaded. There is no account and no model key in the page.

## What you send

- `url`: the YouTube URL or video id
- `why`: a few sentences on why this cut fits the user's goal
- `segments`: start, end, title, reason, quotes
- `answers`: optional `{seconds, question, text}` for questions you already answered from captions
- `open_browser`: leave true so the user's browser opens

The tool fills the outline and the transcript from the cache. You do not paste the whole transcript.

## What the user sees

Left: the YouTube player, a link to the original video, and one card per segment. A card click seeks. **Play all** plays the segments in order.

Right: **Guide** (your why, plus the outline), **Transcript** (the current cue is highlighted; a click seeks), **Q&A / notes** (your answers, plus quotes the user saves). Saved notes stay in `localStorage` in that browser.

The progress bar marks the recommended ranges. When a segment ends, the page shows `next suggested: jump to mm:ss` with Jump and Skip.

## Feedback

Each card has Useful and Not useful. The page stores the choice in two places:

- `POST /feedback` appends `interested` or `not_interested` to `feedback.jsonl` in the state directory
- `localStorage` key `video-guide-feedback-<video_id>` so the click survives if the local server is gone

Notes use `localStorage` only (`video-guide-notes-<video_id>`). They are not sent anywhere.

## If the page fails to open

Return the `url` from the tool so the user can open it. Check that `video-guide mcp` is the process Codex launched, and that captions were fetched in this same state directory (`VIDSNAP_GUIDE_HOME` or `~/.video-guide`).
