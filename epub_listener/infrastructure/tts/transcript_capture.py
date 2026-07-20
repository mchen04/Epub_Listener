"""Shared helpers for capturing per-chapter transcripts during TTS generation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from epub_listener.domain.alignment import (
    RawWordCue,
    build_chunk_sentences,
    build_sentence_cues,
    granularity_for,
)
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.domain.transcript import SentenceCue, chapter_file_to_dict
from epub_listener.infrastructure.utils.durable_file import write_text_durably

logger = logging.getLogger(__name__)


def write_chapter_transcript(
    path: Path,
    chapter_id: str,
    engine: str,
    sentences: list[SentenceCue],
) -> None:
    """Durably persist a chapter transcript so resumed builds keep it."""
    payload = json.dumps(
        chapter_file_to_dict(chapter_id, engine, granularity_for(sentences), tuple(sentences)),
        separators=(",", ":"),
        ensure_ascii=False,
    )
    try:
        write_text_durably(path, payload)
    except OSError as exc:
        raise TTSGenerationError(f"Could not write transcript for {path}: {exc}") from exc


def capture_chapter_transcript(
    path: Path,
    chapter_id: str,
    engine: str,
    text: str,
    word_cues: list[RawWordCue],
    chunks: list[tuple[str, int, int]],
) -> None:
    """Build sentence cues from word cues (or chunk fallback) and persist them."""
    sentences = build_sentence_cues(text, word_cues) if word_cues else build_chunk_sentences(chunks)
    if not sentences:
        logger.warning("No transcript cues captured for chapter %s", chapter_id)
    write_chapter_transcript(path, chapter_id, engine, sentences)


class KokoroTokenWalker:
    """Anchors Kokoro result tokens to their char positions in the chapter text.

    Kokoro's English pipeline yields tokens whose ``text`` reproduces the
    input graphemes in order, with per-token ``start_ts``/``end_ts`` relative
    to each yielded audio chunk. Walking the original text keeps exact char
    anchors; a token that fails to line up falls back to a windowed search.
    """

    _SEARCH_WINDOW = 240

    def __init__(self, text: str) -> None:
        self._text = text
        self._cursor = 0

    def cues_for_chunk(self, tokens: Any, chunk_start_ms: int) -> list[RawWordCue]:
        cues: list[RawWordCue] = []
        for token in tokens or ():
            token_text = getattr(token, "text", None)
            if not token_text:
                continue
            anchor = self._consume(token_text)
            start_ts = getattr(token, "start_ts", None)
            end_ts = getattr(token, "end_ts", None)
            if start_ts is None or end_ts is None:
                continue
            # Kokoro reports timestamps for punctuation tokens too; those are
            # not spoken words and would drag the karaoke marker onto a period.
            if not any(character.isalnum() for character in token_text):
                continue
            start_ms = chunk_start_ms + int(round(float(start_ts) * 1000))
            end_ms = chunk_start_ms + int(round(float(end_ts) * 1000))
            if end_ms < start_ms:
                end_ms = start_ms
            char_start, char_end = anchor if anchor else (None, None)
            cues.append(RawWordCue(token_text, start_ms, end_ms, char_start, char_end))
        return cues

    def _consume(self, token_text: str) -> tuple[int, int] | None:
        text = self._text
        position = self._cursor
        while position < len(text) and text[position].isspace():
            position += 1
        if text.startswith(token_text, position):
            self._cursor = position + len(token_text)
            return (position, self._cursor)
        found = text.find(token_text, position, position + self._SEARCH_WINDOW)
        if found >= 0:
            self._cursor = found + len(token_text)
            return (found, self._cursor)
        return None
