# Watch-along

## Order

1. `transcript_get` so the cache exists. The cue text stays on disk; the tool returns language, kind, duration, and cue count.
2. `transcript_outline`. Omit `description`. Chapters come from the yt-dlp metadata stored with the captions. If the result `source` is `window`, the video had no chapter list and the slices are 5 minutes.
3. Read the user's goal and how many minutes they have. Pick 2–5 sections that fit that budget. Prefer the start of a chapter over a random slice.
4. `transcript_read` each chosen window before you recommend it. If `truncated` is true, read the next slice before you quote the end of it.
5. `open_guide` once the segments are real.

## Fuzzy questions

`search` is lexical. A vague question will miss. Rewrite it into 3–5 short English phrases the speaker might have said, and pass them together as `phrases`. Example: "the bit about hiring too early" becomes `["hired too early", "first hire", "don't hire", "hiring"]`.

Use the video's spoken language for those phrases. `transcript_get` tells you `language`. If the user wrote in another language, translate the phrasings into the caption language. Do not search only with the user's original wording when it will not appear in the cues.

Read `context_before` and `context_after` before you decide the hit is the right moment.

## Segments you pass to open_guide

Each segment needs:

- `start` and `end` in seconds, taken from an outline section or a search hit
- `title`, short
- `reason`, why this range serves the user's goal or time budget
- `quotes`, one or more strings copied from `text` fields

`open_guide` drops a quote that is not in the cached captions and counts it in `dropped_quotes`. If that count is not zero, fetch the real cue and call `open_guide` again. Do not leave a card without evidence.

## Time budget

If the user has 10 minutes, the sum of `(end - start)` for the segments you recommend should stay near that budget. Say what you skipped and why. Offer the outline timestamps so they can jump off your path.
