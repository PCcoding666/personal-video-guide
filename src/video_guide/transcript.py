"""Outline a transcript and read one time window.

Chapters come from description lines such as ``00:00 Intro``. Two or more of
those lines become the outline. Otherwise the transcript is sliced into
five-minute windows, because many real feeds have no chapter list. A read
returns the cues that overlap the requested interval and stops at 8,000
characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .links import format_timestamp
from .models import Sentence, Transcript, Window

MAX_READ_CHARS = 8_000
OUTLINE_WINDOW_SECONDS = 300.0
PREVIEW_SENTENCES = 3
MIN_CHAPTERS = 2

_CHAPTER_LINE = re.compile(
    r"""
    ^\s*
    (?:[\[(])?
    (?:(?P<h>\d{1,2}):)?
    (?P<m>\d{1,2}):(?P<s>\d{2})
    (?:[\])])?
    (?:\s*[-–—:]\s*|\s+)
    (?P<title>\S.*?)\s*$
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ReadResult:
    """Cues overlapping ``[start, end)``, possibly cut off at the character budget."""

    start: float
    end: float
    sentences: tuple[Sentence, ...]
    text: str
    truncated: bool


def parse_chapters(description: str) -> list[tuple[float, str]]:
    """Return ``(start_seconds, title)`` for description lines that are chapters.

    A timestamp has to sit at the start of the line (optional brackets). A
    mention such as ``see 12:34 for the demo`` is left alone.
    """
    found: list[tuple[float, str]] = []
    for line in description.splitlines():
        match = _CHAPTER_LINE.match(line)
        if match is None:
            continue
        start = _chapter_seconds(match.group("h"), match.group("m"), match.group("s"))
        title = match.group("title").strip()
        if start is None or not title:
            continue
        found.append((start, title))
    found.sort(key=lambda item: item[0])
    deduped: list[tuple[float, str]] = []
    seen: set[float] = set()
    for start, title in found:
        if start in seen:
            continue
        seen.add(start)
        deduped.append((start, title))
    return deduped


def outline_transcript(transcript: Transcript, description: str = "") -> list[Window]:
    """Build chapter windows, or 5-minute windows when there are fewer than two chapters."""
    chapters = parse_chapters(description)
    if len(chapters) >= MIN_CHAPTERS:
        labeled = _chapter_spans(chapters, transcript.duration)
        source: Literal["chapters", "window"] = "chapters"
    else:
        labeled = _time_windows(transcript.duration)
        source = "window"
    windows: list[Window] = []
    for start, end, title in labeled:
        preview = tuple(
            sentence.text for sentence in transcript.sentences if _in_section(sentence, start, end)
        )[:PREVIEW_SENTENCES]
        windows.append(Window(start=start, end=end, title=title, source=source, preview=preview))
    return windows


def read_transcript(
    transcript: Transcript,
    start: float,
    end: float,
    *,
    max_chars: int = MAX_READ_CHARS,
) -> ReadResult:
    """Return cues that overlap ``[start, end)``.

    Whole cues are kept until one more would pass ``max_chars``. That cue is
    cut to the remaining budget and ``truncated`` is set. The default budget
    is 8,000 characters.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    overlapping = [
        sentence
        for sentence in transcript.sentences
        if sentence.start < end and sentence.end > start
    ]
    included: list[Sentence] = []
    parts: list[str] = []
    used = 0
    truncated = False
    for sentence in overlapping:
        separator = 1 if parts else 0
        piece = sentence.text
        if used + separator + len(piece) <= max_chars:
            parts.append(piece)
            included.append(sentence)
            used += separator + len(piece)
            continue
        truncated = True
        room = max_chars - used - separator
        if room > 0:
            parts.append(piece[:room])
            included.append(Sentence(start=sentence.start, end=sentence.end, text=piece[:room]))
        break
    return ReadResult(
        start=start,
        end=end,
        sentences=tuple(included),
        text="\n".join(parts),
        truncated=truncated,
    )


def _chapter_seconds(hours: str | None, minutes: str, seconds: str) -> float | None:
    sec = int(seconds)
    minute = int(minutes)
    if sec >= 60:
        return None
    if hours is not None and minute >= 60:
        return None
    hour = int(hours) if hours is not None else 0
    return float(hour * 3600 + minute * 60 + sec)


def _chapter_spans(
    chapters: list[tuple[float, str]], duration: float
) -> list[tuple[float, float, str]]:
    spans: list[tuple[float, float, str]] = []
    for index, (start, title) in enumerate(chapters):
        if index + 1 < len(chapters):
            end = chapters[index + 1][0]
        else:
            end = max(start, duration)
        end = max(end, start)
        spans.append((start, end, title))
    return spans


def _time_windows(duration: float) -> list[tuple[float, float, str]]:
    if duration <= 0:
        return []
    spans: list[tuple[float, float, str]] = []
    start = 0.0
    while start < duration and len(spans) < 10_000:
        end = min(duration, start + OUTLINE_WINDOW_SECONDS)
        title = f"{format_timestamp(start)}–{format_timestamp(end)}"
        spans.append((start, end, title))
        if end <= start:
            break
        start = end
    return spans


def _in_section(sentence: Sentence, start: float, end: float) -> bool:
    if end > start:
        return start <= sentence.start < end
    return sentence.start == start
