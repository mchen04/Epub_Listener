"""Sentence segmentation and word-cue alignment for transcripts.

The TTS engines report word timings against the spoken stream; this module
maps them back onto the chapter's display text so a reader UI can highlight
the sentence being narrated and mark the exact word inside it.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from epub_listener.domain.transcript import (
    GRANULARITY_SENTENCE,
    GRANULARITY_WORD,
    SentenceCue,
    WordCue,
)

# A sentence ends at ., !, ?, or … (optionally followed by closing quotes or
# brackets) before whitespace, or at any newline. Abbreviation false-positives
# only shorten a highlight unit; they never lose text.
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)|\n")
# When prose has no terminator for a very long run, split at a space so a
# highlight unit stays a readable size.
_MAX_SENTENCE_CHARS = 400


@dataclass(frozen=True)
class RawWordCue:
    """An engine-reported word: chapter-relative ms, optionally text-anchored.

    ``char_start``/``char_end`` are offsets into the chapter text when the
    engine knows them (Kokoro tokens); ``None`` for engines that only report
    the spoken word (Edge), which this module anchors by scanning.
    """

    text: str
    start_ms: int
    end_ms: int
    char_start: int | None = None
    char_end: int | None = None


def split_sentence_spans(text: str) -> list[tuple[int, int]]:
    """Char spans of display sentences: trimmed, ordered, non-overlapping."""
    boundaries: list[int] = []
    for match in _SENTENCE_END.finditer(text):
        boundaries.append(match.end())
    boundaries.append(len(text))

    spans: list[tuple[int, int]] = []
    cursor = 0
    for boundary in boundaries:
        start, end = cursor, boundary
        cursor = boundary
        while end - start > _MAX_SENTENCE_CHARS:
            split_at = text.rfind(" ", start, start + _MAX_SENTENCE_CHARS)
            if split_at <= start:
                split_at = start + _MAX_SENTENCE_CHARS
            piece = _trimmed_span(text, start, split_at)
            if piece:
                spans.append(piece)
            start = split_at
        piece = _trimmed_span(text, start, end)
        if piece:
            spans.append(piece)
    return spans


def _trimmed_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def anchor_cues(text: str, cues: list[RawWordCue]) -> list[RawWordCue]:
    """Fill in char anchors for cues that lack them by scanning the text.

    Engines like Edge report the source word for each boundary event, in
    reading order. Normalization can expand one written token into several
    spoken words ("123" -> "one twenty three"); unmatched cues keep an empty
    anchor at the current scan position so they still land in the right
    sentence.
    """
    lowered = text.lower()
    anchored: list[RawWordCue] = []
    cursor = 0
    for cue in cues:
        if cue.char_start is not None and cue.char_end is not None:
            anchored.append(cue)
            cursor = max(cursor, cue.char_end)
            continue
        needle = cue.text.strip().lower()
        window_end = min(len(text), cursor + max(240, 4 * len(needle)))
        found = lowered.find(needle, cursor, window_end) if needle else -1
        if found >= 0 and _is_word_aligned(lowered, found, len(needle)):
            anchored.append(
                RawWordCue(cue.text, cue.start_ms, cue.end_ms, found, found + len(needle))
            )
            cursor = found + len(needle)
        else:
            # Normalized speech ("123" spoken as "one twenty three"): mark the
            # written token at the scan position for every spoken word of it,
            # without consuming it, so the display still tracks the narration.
            token_start, token_end = _token_at(text, cursor)
            anchored.append(RawWordCue(cue.text, cue.start_ms, cue.end_ms, token_start, token_end))
    return anchored


def _token_at(text: str, cursor: int) -> tuple[int, int]:
    start = cursor
    while start < len(text) and text[start].isspace():
        start += 1
    end = start
    while end < len(text) and not text[end].isspace():
        end += 1
    return (start, end) if end > start else (cursor, cursor)


def _is_word_aligned(lowered: str, start: int, length: int) -> bool:
    before = lowered[start - 1] if start > 0 else " "
    return not before.isalnum()


def build_sentence_cues(text: str, cues: list[RawWordCue]) -> list[SentenceCue]:
    """Assemble display sentences with word cues from anchored raw cues."""
    spans = split_sentence_spans(text)
    if not spans:
        return []
    anchored = anchor_cues(text, cues)
    span_starts = [start for start, _ in spans]
    words_by_span: list[list[WordCue]] = [[] for _ in spans]
    for cue in anchored:
        assert cue.char_start is not None and cue.char_end is not None
        span_index = min(len(spans) - 1, max(0, bisect_right(span_starts, cue.char_start) - 1))
        start, end = spans[span_index]
        char_start = min(max(cue.char_start, start), end) - start
        char_end = min(max(cue.char_end, start), end) - start
        words_by_span[span_index].append(
            WordCue(cue.text, cue.start_ms, cue.end_ms, char_start, max(char_start, char_end))
        )

    # Sentence starts must be non-decreasing for consumers' binary search;
    # un-narrated spans (e.g. stray punctuation) keep their text visible with
    # a zero-length interval at the running position.
    sentences: list[SentenceCue] = []
    running_start = 0
    for (start, end), words in zip(spans, words_by_span, strict=True):
        if words:
            start_ms = max(running_start, min(word.start_ms for word in words))
            end_ms = max(start_ms, max(word.end_ms for word in words))
        else:
            start_ms = end_ms = running_start
        sentences.append(SentenceCue(text[start:end], start_ms, end_ms, tuple(words)))
        running_start = start_ms
    return sentences


def build_chunk_sentences(chunks: list[tuple[str, int, int]]) -> list[SentenceCue]:
    """Sentence-granularity fallback from (text, start_ms, end_ms) chunks."""
    sentences: list[SentenceCue] = []
    running = 0
    for text, start_ms, end_ms in chunks:
        cleaned = text.strip()
        if not cleaned:
            continue
        start = max(int(start_ms), running)
        sentences.append(SentenceCue(cleaned, start, max(int(end_ms), start), ()))
        running = start
    return sentences


def granularity_for(sentences: list[SentenceCue]) -> str:
    return (
        GRANULARITY_WORD if any(sentence.words for sentence in sentences) else GRANULARITY_SENTENCE
    )
