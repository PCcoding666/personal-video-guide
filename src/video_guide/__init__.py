"""Personal video guide: a local, single-user tool package.

The guide discovers new videos from user-added feeds, fetches caption tracks,
and outlines, reads, searches, and links transcript windows. It runs only when
a host agent or the user invokes it. It never schedules itself, sends
notifications, or calls a model, and it depends on neither the core runtime nor
the providers. Its own HTTP client is limited to allow-listed HTTPS GET
requests. YouTube captions are the exception: they are read by a ``yt-dlp`` the
user installed, with subtitle-only arguments. The package never downloads media
and never bundles anti-bot bypasses.
"""
