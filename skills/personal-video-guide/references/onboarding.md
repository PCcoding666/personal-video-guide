# Onboarding

Ask before you store anything:

- What are you trying to learn or decide?
- Which channels or podcasts should the guide follow?
- How many minutes can a recommendation take?
- What time of day should a host remind you? The host creates that schedule. This package does not.

Caption setup, once, on the machine where Codex runs:

- Install `yt-dlp` yourself, plus a JavaScript runtime (`deno`) and `yt-dlp-ejs` (`pip install -U "yt-dlp[default]"`).
- Export a Netscape `cookies.txt` from a browser that is signed in to YouTube. Then `profile_set` with `cookies` set to that path. The package never reads the file.
- If the spoken language is not English, `profile_set` with `caption_language` (for example `zh-Hans`). A Latin title otherwise prefers `en` or `en-orig`.

`profile_get` shows what is saved. Do not ask the user to paste cookie contents into the chat.
