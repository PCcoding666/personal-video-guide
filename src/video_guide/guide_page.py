"""Local guide page for watch-along.

The page embeds YouTube's own player. It does not download media and it does
not talk to any service except YouTube's iframe and this process on
127.0.0.1. Guide JSON is written under the state directory. Useful / not
useful clicks are appended to the feedback log.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .models import FeedbackKind
from .state import GuideStore

_GUIDE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video guide</title>
<style>
  :root {
    --paper: #f4efe6;
    --ink: #1c1915;
    --muted: #5c564c;
    --line: #d9d0c3;
    --card: #fffaf3;
    --accent: #9a3412;
    --accent-soft: #f3d6c8;
    --good: #1f6b4a;
    --bad: #8f2d2d;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font: 16px/1.45 "Iowan Old Style", Palatino, Georgia, serif;
    color: var(--ink);
    background: var(--paper);
  }
  header {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: baseline;
    padding: 0.8rem 1.2rem;
    border-bottom: 1px solid var(--line);
  }
  header h1 { font-size: 1.15rem; margin: 0; }
  header a { color: var(--accent); }
  main {
    display: grid;
    grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.9fr);
    min-height: calc(100vh - 3.2rem);
  }
  .stage, .side { padding: 1rem 1.2rem 2rem; }
  .side { border-left: 1px solid var(--line); background: #fbf7f1; }
  .frame {
    position: relative;
    background: #111;
    aspect-ratio: 16/9;
  }
  .frame iframe, #player { width: 100%; height: 100%; }
  .bar {
    position: relative;
    height: 10px;
    margin: 0.7rem 0 0.4rem;
    background: #e6dfd4;
    border-radius: 99px;
    cursor: pointer;
  }
  .bar i {
    position: absolute;
    top: 0;
    bottom: 0;
    background: var(--accent);
    opacity: 0.85;
  }
  .playhead {
    position: absolute;
    top: -3px;
    width: 2px;
    height: 16px;
    background: var(--ink);
  }
  .banner {
    display: none;
    margin: 0.6rem 0;
    padding: 0.7rem 0.8rem;
    background: var(--accent-soft);
    border-radius: 8px;
  }
  .banner strong { display: block; }
  button, .tab {
    font: inherit;
    cursor: pointer;
    border: 1px solid var(--line);
    background: var(--card);
    border-radius: 999px;
    padding: 0.25rem 0.75rem;
  }
  button.primary { background: var(--accent); color: white; border-color: var(--accent); }
  .cards { display: flex; flex-direction: column; gap: 0.7rem; margin-top: 0.8rem; }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 0.75rem 0.9rem;
  }
  .card.active { border-color: var(--accent); }
  .card h2 { font-size: 1rem; margin: 0 0 0.2rem; }
  .meta { color: var(--muted); font-size: 0.85rem; }
  blockquote { margin: 0.4rem 0; color: var(--ink); }
  .tabs { display: flex; gap: 0.4rem; margin-bottom: 0.8rem; }
  .tab[aria-selected="true"] { background: var(--ink); color: var(--paper); }
  .panel { display: none; }
  .panel.active { display: block; }
  .sentence { padding: 0.15rem 0.2rem; border-radius: 4px; cursor: pointer; }
  .sentence.now { background: var(--accent-soft); }
  .feedback { display: flex; gap: 0.4rem; margin-top: 0.6rem; }
  .note { font-size: 0.92rem; border-bottom: 1px solid var(--line); padding: 0.35rem 0; }
  @media (max-width: 860px) {
    main { grid-template-columns: 1fr; }
    .side { border-left: 0; border-top: 1px solid var(--line); }
  }
</style>
</head>
<body>
<header>
  <h1 id="title">Video guide</h1>
  <a id="original" href="#" target="_blank" rel="noopener">Open on YouTube</a>
</header>
<main>
  <section class="stage">
    <div class="frame"><div id="player"></div></div>
    <div class="bar" id="bar" title="Click a recommended range"></div>
    <div id="banner" class="banner" role="status"></div>
    <div style="display:flex;gap:0.4rem;align-items:center">
      <button class="primary" id="play-all" type="button">Play all</button>
      <span class="meta" id="clock">0:00</span>
    </div>
    <div class="cards" id="cards"></div>
  </section>
  <section class="side">
    <div class="tabs" role="tablist">
      <button class="tab" type="button" data-tab="guide" aria-selected="true">Guide</button>
      <button class="tab" type="button" data-tab="transcript" aria-selected="false">Transcript</button>
      <button class="tab" type="button" data-tab="notes" aria-selected="false">Q&amp;A / notes</button>
    </div>
    <div class="panel active" id="panel-guide"></div>
    <div class="panel" id="panel-transcript"></div>
    <div class="panel" id="panel-notes"></div>
  </section>
</main>
<script id="guide-data" type="application/json">__GUIDE_JSON__</script>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
const GUIDE = JSON.parse(document.getElementById("guide-data").textContent);
const $ = (id) => document.getElementById(id);
let player = null;
let duration = 0;
let playAll = false;
let playIndex = 0;
let prompted = false;
let advancing = false;
const notesKey = "video-guide-notes-" + GUIDE.video_id;

function clock(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  return m + ":" + String(s).padStart(2, "0");
}
function seek(seconds) {
  if (player && player.seekTo) {
    player.seekTo(seconds, true);
    player.playVideo();
  }
}
function currentSegment(time) {
  return (GUIDE.segments || []).find((seg) => time >= seg.start && time < seg.end) || null;
}
function nextSegment(time) {
  return (GUIDE.segments || []).find((seg) => seg.start >= time - 0.2) || null;
}

$("title").textContent = GUIDE.title || "Video guide";
$("original").href = GUIDE.url;
document.title = GUIDE.title || "Video guide";

function renderCards() {
  const root = $("cards");
  root.replaceChildren();
  (GUIDE.segments || []).forEach((seg, index) => {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.start = String(seg.start);
    const h = document.createElement("h2");
    h.textContent = clock(seg.start) + "–" + clock(seg.end) + "  " + (seg.title || "");
    const reason = document.createElement("p");
    reason.textContent = seg.reason || "";
    card.append(h, reason);
    (seg.quotes || []).forEach((quote) => {
      const q = document.createElement("blockquote");
      q.textContent = "“" + quote + "”";
      const save = document.createElement("button");
      save.type = "button";
      save.textContent = "Save quote";
      save.addEventListener("click", (event) => {
        event.stopPropagation();
        saveNote(clock(seg.start) + " " + quote);
      });
      card.append(q, save);
    });
    const row = document.createElement("div");
    row.className = "feedback";
    const jump = document.createElement("button");
    jump.type = "button";
    jump.className = "primary";
    jump.textContent = "Play from here";
    jump.addEventListener("click", (event) => {
      event.stopPropagation();
      playAll = false;
      seek(seg.start);
    });
    row.append(jump);
    ["Useful", "Not useful"].forEach((label) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        sendFeedback(label === "Useful");
        button.textContent = "Saved";
      });
      row.append(button);
    });
    card.append(row);
    card.addEventListener("click", () => { playAll = false; seek(seg.start); });
    root.append(card);
  });
}

function renderBar() {
  const bar = $("bar");
  bar.replaceChildren();
  const total = duration || GUIDE.duration || 1;
  (GUIDE.segments || []).forEach((seg) => {
    const mark = document.createElement("i");
    mark.style.left = (100 * seg.start / total) + "%";
    mark.style.width = Math.max(0.8, 100 * (seg.end - seg.start) / total) + "%";
    mark.title = seg.title || "";
    mark.addEventListener("click", (event) => {
      event.stopPropagation();
      seek(seg.start);
    });
    bar.append(mark);
  });
  const head = document.createElement("span");
  head.className = "playhead";
  head.id = "playhead";
  bar.append(head);
}

function renderGuide() {
  const panel = $("panel-guide");
  panel.replaceChildren();
  const why = document.createElement("p");
  why.textContent = GUIDE.why || "No overview yet.";
  panel.append(why);
  (GUIDE.outline || []).forEach((section) => {
    const row = document.createElement("p");
    const link = document.createElement("button");
    link.type = "button";
    link.textContent = (section.timestamp || clock(section.start)) + " " + (section.title || "");
    link.addEventListener("click", () => seek(section.start));
    row.append(link);
    panel.append(row);
  });
}

function renderTranscript() {
  const panel = $("panel-transcript");
  panel.replaceChildren();
  (GUIDE.sentences || []).forEach((sentence) => {
    const row = document.createElement("div");
    row.className = "sentence";
    row.dataset.start = String(sentence.start);
    row.dataset.end = String(sentence.end);
    row.textContent = clock(sentence.start) + "  " + sentence.text;
    row.addEventListener("click", () => seek(sentence.start));
    panel.append(row);
  });
}

function renderNotes() {
  const panel = $("panel-notes");
  panel.replaceChildren();
  (GUIDE.answers || []).forEach((answer) => {
    const block = document.createElement("article");
    block.className = "note";
    const q = document.createElement("strong");
    q.textContent = (answer.timestamp || clock(answer.seconds)) + " " + (answer.question || "");
    const body = document.createElement("p");
    body.textContent = answer.text || "";
    const jump = document.createElement("button");
    jump.type = "button";
    jump.textContent = "Jump";
    jump.addEventListener("click", () => seek(answer.seconds || 0));
    block.append(q, body, jump);
    panel.append(block);
  });
  const saved = document.createElement("div");
  saved.id = "saved-notes";
  panel.append(saved);
  paintNotes();
}

function paintNotes() {
  const saved = $("saved-notes");
  if (!saved) return;
  saved.replaceChildren();
  const notes = JSON.parse(localStorage.getItem(notesKey) || "[]");
  notes.forEach((note) => {
    const row = document.createElement("p");
    row.className = "note";
    row.textContent = note;
    saved.append(row);
  });
}
function saveNote(text) {
  const notes = JSON.parse(localStorage.getItem(notesKey) || "[]");
  notes.unshift(text);
  localStorage.setItem(notesKey, JSON.stringify(notes.slice(0, 40)));
  paintNotes();
}
function sendFeedback(useful) {
  const key = "video-guide-feedback-" + GUIDE.video_id;
  localStorage.setItem(key, useful ? "useful" : "not-useful");
  fetch("/feedback", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({video_id: GUIDE.video_id, useful: useful})
  }).catch(() => {});
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.setAttribute("aria-selected", "false"));
    document.querySelectorAll(".panel").forEach((panel) => panel.classList.remove("active"));
    tab.setAttribute("aria-selected", "true");
    $("panel-" + tab.dataset.tab).classList.add("active");
  });
});
$("play-all").addEventListener("click", () => {
  if (!GUIDE.segments || !GUIDE.segments.length) return;
  playAll = true;
  playIndex = 0;
  prompted = false;
  seek(GUIDE.segments[0].start);
});

function showBanner(segment) {
  const banner = $("banner");
  banner.style.display = "block";
  banner.replaceChildren();
  const label = document.createElement("strong");
  label.textContent = "next suggested: jump to " + clock(segment.start);
  const row = document.createElement("div");
  row.className = "feedback";
  const jump = document.createElement("button");
  jump.type = "button";
  jump.className = "primary";
  jump.textContent = "Jump";
  jump.addEventListener("click", () => { prompted = false; seek(segment.start); banner.style.display = "none"; });
  const skip = document.createElement("button");
  skip.type = "button";
  skip.textContent = "Skip";
  skip.addEventListener("click", () => { banner.style.display = "none"; prompted = true; });
  row.append(jump, skip);
  banner.append(label, row);
}

function tick() {
  if (!player || !player.getCurrentTime) return;
  const time = player.getCurrentTime() || 0;
  $("clock").textContent = clock(time);
  const head = $("playhead");
  const total = duration || player.getDuration() || 0;
  if (head && total) head.style.left = (100 * time / total) + "%";
  const insideCard = currentSegment(time);
  document.querySelectorAll(".card").forEach((card) => {
    card.classList.toggle("active", insideCard && Number(card.dataset.start) === insideCard.start);
  });
  document.querySelectorAll(".sentence").forEach((row) => {
    const start = Number(row.dataset.start);
    const end = Number(row.dataset.end);
    const on = time >= start && time < end;
    row.classList.toggle("now", on);
    if (on) row.scrollIntoView({block: "nearest"});
  });
  const segments = GUIDE.segments || [];
  const inside = insideCard;
  if (playAll && inside && time >= inside.end - 0.35 && !advancing) {
    advancing = true;
    playIndex += 1;
    if (segments[playIndex]) seek(segments[playIndex].start);
    else playAll = false;
  }
  if (!inside || time < inside.end - 1) advancing = false;
  if (!playAll && inside && time >= inside.end - 0.35 && !prompted) {
    const upcoming = segments.find((seg) => seg.start >= inside.end - 0.2);
    if (upcoming) { showBanner(upcoming); prompted = true; }
  }
  if (inside && time < inside.end - 1) prompted = false;
}
setInterval(tick, 400);

function onYouTubeIframeAPIReady() {
  player = new YT.Player("player", {
    videoId: GUIDE.video_id,
    playerVars: {rel: 0, modestbranding: 1, origin: location.origin},
    events: {
      onReady: () => {
        duration = player.getDuration() || GUIDE.duration || 0;
        renderBar();
      }
    }
  });
}
window.onYouTubeIframeAPIReady = onYouTubeIframeAPIReady;
renderCards();
renderGuide();
renderTranscript();
renderNotes();
renderBar();
</script>
</body>
</html>
"""


class _GuideState:
    def __init__(self) -> None:
        self.document: dict[str, Any] = {}
        self.store: GuideStore | None = None
        self.html = ""


_STATE = _GuideState()
_SERVER: ThreadingHTTPServer | None = None
_THREAD: threading.Thread | None = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            body = _STATE.html.encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/guide.json":
            body = json.dumps(_STATE.document).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/feedback":
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, "application/json", b'{"ok":false}')
            return
        useful = bool(payload.get("useful"))
        video_id = str(payload.get("video_id") or "")
        served = str(_STATE.document.get("video_id") or "")
        if not video_id or video_id != served:
            self._send(400, "application/json", b'{"ok":false}')
            return
        if _STATE.store is not None:
            kind = FeedbackKind.INTERESTED if useful else FeedbackKind.NOT_INTERESTED
            _STATE.store.add_feedback(video_id, kind, now=datetime.now(timezone.utc))
        self._send(200, "application/json", b'{"ok":true}')

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def render_guide_html(document: dict[str, Any]) -> str:
    """Inline the guide JSON. ``<`` is escaped so the payload cannot break the page."""
    payload = json.dumps(document, ensure_ascii=False).replace("<", "\\u003c")
    return _GUIDE_HTML.replace("__GUIDE_JSON__", payload)


def write_guide_json(store: GuideStore, document: dict[str, Any]) -> None:
    """Atomically store the guide document under the state directory."""
    video_id = str(document.get("video_id") or "")
    path = store.home / "guides" / f"{video_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def publish_guide(
    document: dict[str, Any],
    *,
    store: GuideStore,
    open_browser: bool = False,
) -> str:
    """Write the guide file and serve the page on 127.0.0.1. Return the page URL."""
    write_guide_json(store, document)
    return serve_guide(document, store=store, open_browser=open_browser)


def serve_guide(
    document: dict[str, Any],
    *,
    store: GuideStore | None = None,
    open_browser: bool = False,
) -> str:
    """Serve the guide on 127.0.0.1 and return its URL. Reuses one local server."""
    global _SERVER, _THREAD
    _STATE.document = document
    _STATE.store = store
    _STATE.html = render_guide_html(document)
    if _SERVER is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, name="video-guide-page", daemon=True)
        thread.start()
        _SERVER = server
        _THREAD = thread
    port = _SERVER.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        webbrowser.open(url)
    return url


def shutdown_guide_server() -> None:
    """Stop the local page server. Tests use this so ports do not linger."""
    global _SERVER, _THREAD
    server = _SERVER
    _SERVER = None
    if server is not None:
        server.shutdown()
        server.server_close()
    _THREAD = None
