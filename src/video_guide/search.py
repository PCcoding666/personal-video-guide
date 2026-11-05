"""Lexical search over a transcript.

Scoring is Okapi BM25 over case-folded tokens, summed across every query
phrase the host supplies. An exact phrase (ignoring case and repeated
whitespace) also gets a fixed bonus so a remembered quote outranks a bag of
the same words. Ties break by earlier start time, then by cue order. The
standard library is enough; there is no vector index and no model call.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .links import LinkError, link_at
from .models import Hit, Transcript

SEARCH_LIMIT = 8
_K1 = 1.5
_B = 0.75
_PHRASE_BONUS = 5.0
_TOKEN = re.compile(r"[0-9A-Za-z]+|[\u3400-\u9fff]")


@dataclass(frozen=True)
class SearchResult:
    """Ranked hits plus the time span the captions actually cover."""

    hits: tuple[Hit, ...]
    coverage_start: float
    coverage_end: float
    sentence_count: int


def tokenize(text: str) -> list[str]:
    """Case-fold words. Each CJK character is its own token."""
    return [match.group(0).casefold() for match in _TOKEN.finditer(text)]


def search_transcript(
    transcript: Transcript,
    phrases: Sequence[str],
    *,
    limit: int = SEARCH_LIMIT,
) -> SearchResult:
    """Score ``phrases`` against every cue. No match yields an empty hit list.

    Each hit carries the cue, the neighboring cues, and a tool-built playback
    link when ``transcript.video_id`` is a YouTube id.
    """
    # Context is the previous and next cue in time, whatever order the file used.
    sentences = tuple(
        sorted(transcript.sentences, key=lambda sentence: (sentence.start, sentence.end))
    )
    if sentences:
        coverage_start = min(sentence.start for sentence in sentences)
        coverage_end = max(sentence.end for sentence in sentences)
    else:
        coverage_start = 0.0
        coverage_end = 0.0
    result = SearchResult(
        hits=(),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        sentence_count=len(sentences),
    )
    cleaned = [phrase.strip() for phrase in phrases if phrase and phrase.strip()]
    if not sentences or not cleaned or limit < 1:
        return result
    docs = [tokenize(sentence.text) for sentence in sentences]
    queries = [tokenize(phrase) for phrase in cleaned]
    scores = _bm25(docs, queries)
    for phrase in cleaned:
        needle = _squash(phrase)
        if len(needle) < 2:
            continue
        bonus = _PHRASE_BONUS + len(tokenize(phrase))
        for index, sentence in enumerate(sentences):
            if needle in _squash(sentence.text):
                scores[index] += bonus
    ranked = sorted(
        (
            (scores[index], sentence.start, index)
            for index, sentence in enumerate(sentences)
            if scores[index] > 0
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    hits: list[Hit] = []
    for score, _start, index in ranked[:limit]:
        sentence = sentences[index]
        before = sentences[index - 1].text if index > 0 else None
        after = sentences[index + 1].text if index + 1 < len(sentences) else None
        hits.append(
            Hit(
                start=sentence.start,
                end=sentence.end,
                text=sentence.text,
                score=score,
                context_before=before,
                context_after=after,
                url=_hit_url(transcript.video_id, sentence.start),
            )
        )
    return SearchResult(
        hits=tuple(hits),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        sentence_count=len(sentences),
    )


def _bm25(docs: list[list[str]], queries: list[list[str]]) -> list[float]:
    count = len(docs)
    scores = [0.0] * count
    if count == 0:
        return scores
    document_frequency: dict[str, int] = {}
    term_freqs: list[dict[str, int]] = []
    lengths: list[int] = []
    for doc in docs:
        frequencies: dict[str, int] = {}
        for token in doc:
            frequencies[token] = frequencies.get(token, 0) + 1
        term_freqs.append(frequencies)
        lengths.append(len(doc))
        for token in frequencies:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    average = sum(lengths) / count or 1.0
    for query in queries:
        if not query:
            continue
        query_frequency: dict[str, int] = {}
        for token in query:
            query_frequency[token] = query_frequency.get(token, 0) + 1
        for token, repeats in query_frequency.items():
            containing = document_frequency.get(token, 0)
            if containing == 0:
                continue
            idf = math.log(1.0 + (count - containing + 0.5) / (containing + 0.5))
            for index, frequencies in enumerate(term_freqs):
                freq = frequencies.get(token, 0)
                if freq == 0:
                    continue
                denominator = freq + _K1 * (1.0 - _B + _B * lengths[index] / average)
                scores[index] += idf * (freq * (_K1 + 1.0) / denominator) * repeats
    return scores


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _hit_url(video_id: str, start: float) -> str | None:
    try:
        return link_at(video_id, start).url
    except LinkError:
        return None
