"""Transcript domain model and validation (see docs/transcript-format.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from epub_listener.domain.exceptions import TranscriptError

TRANSCRIPT_FORMAT = "epub-listener-transcript"
CHAPTER_TRANSCRIPT_FORMAT = "epub-listener-chapter-transcript"
TRANSCRIPT_VERSION = 1
GRANULARITY_WORD = "word"
GRANULARITY_SENTENCE = "sentence"
GRANULARITIES = (GRANULARITY_WORD, GRANULARITY_SENTENCE)
GEOB_DESCRIPTION = "EPUB_LISTENER_TRANSCRIPT"
GEOB_MIME = "application/gzip"
GEOB_FILENAME = "transcript.json.gz"


@dataclass(frozen=True)
class WordCue:
    """A spoken word: chapter-relative ms plus a char range into the sentence text."""

    text: str
    start_ms: int
    end_ms: int
    char_start: int
    char_end: int


@dataclass(frozen=True)
class SentenceCue:
    text: str
    start_ms: int
    end_ms: int
    words: tuple[WordCue, ...] = ()


@dataclass(frozen=True)
class ChapterTranscript:
    """One chapter's cues; ``index`` refers to the MP3's embedded chapter order."""

    index: int
    title: str
    granularity: str
    sentences: tuple[SentenceCue, ...]


@dataclass(frozen=True)
class BookTranscript:
    producer: str
    engine: str
    generation_key: str
    language: str
    chapters: tuple[ChapterTranscript, ...]


def sentence_to_dict(sentence: SentenceCue) -> dict[str, Any]:
    return {
        "text": sentence.text,
        "start": sentence.start_ms,
        "end": sentence.end_ms,
        "words": [
            {
                "text": word.text,
                "start": word.start_ms,
                "end": word.end_ms,
                "charStart": word.char_start,
                "charEnd": word.char_end,
            }
            for word in sentence.words
        ],
    }


def book_transcript_to_dict(transcript: BookTranscript) -> dict[str, Any]:
    return {
        "format": TRANSCRIPT_FORMAT,
        "version": TRANSCRIPT_VERSION,
        "producer": transcript.producer,
        "engine": transcript.engine,
        "generationKey": transcript.generation_key,
        "language": transcript.language,
        "chapters": [
            {
                "index": chapter.index,
                "title": chapter.title,
                "granularity": chapter.granularity,
                "sentences": [sentence_to_dict(sentence) for sentence in chapter.sentences],
            }
            for chapter in transcript.chapters
        ],
    }


def chapter_file_to_dict(
    chapter_id: str,
    engine: str,
    granularity: str,
    sentences: tuple[SentenceCue, ...],
) -> dict[str, Any]:
    """Per-chapter workspace file: same sentence shape, no book-level index yet."""
    return {
        "format": CHAPTER_TRANSCRIPT_FORMAT,
        "version": TRANSCRIPT_VERSION,
        "chapterId": chapter_id,
        "engine": engine,
        "granularity": granularity,
        "sentences": [sentence_to_dict(sentence) for sentence in sentences],
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TranscriptError(message)


def _is_ms(value: Any) -> bool:
    return type(value) is int and value >= 0


def parse_sentences(raw: Any, *, where: str) -> tuple[SentenceCue, ...]:
    _require(isinstance(raw, list), f"{where}: sentences must be a list")
    sentences: list[SentenceCue] = []
    previous_start = 0
    for position, entry in enumerate(raw):
        label = f"{where} sentence {position}"
        _require(isinstance(entry, dict), f"{label}: must be an object")
        text = entry.get("text")
        _require(isinstance(text, str) and bool(text.strip()), f"{label}: text must be non-empty")
        start = entry.get("start")
        end = entry.get("end")
        _require(_is_ms(start) and _is_ms(end) and start <= end, f"{label}: invalid start/end")
        _require(start >= previous_start, f"{label}: sentence starts must be non-decreasing")
        previous_start = start
        words_raw = entry.get("words")
        _require(isinstance(words_raw, list), f"{label}: words must be a list")
        words: list[WordCue] = []
        previous_word_start = 0
        for word_position, word_entry in enumerate(words_raw):
            word_label = f"{label} word {word_position}"
            _require(isinstance(word_entry, dict), f"{word_label}: must be an object")
            word_text = word_entry.get("text")
            _require(
                isinstance(word_text, str) and bool(word_text),
                f"{word_label}: text must be non-empty",
            )
            word_start = word_entry.get("start")
            word_end = word_entry.get("end")
            _require(
                _is_ms(word_start) and _is_ms(word_end) and word_start <= word_end,
                f"{word_label}: invalid start/end",
            )
            _require(
                word_start >= previous_word_start,
                f"{word_label}: word starts must be non-decreasing",
            )
            previous_word_start = word_start
            char_start = word_entry.get("charStart")
            char_end = word_entry.get("charEnd")
            _require(
                type(char_start) is int
                and type(char_end) is int
                and 0 <= char_start <= char_end <= len(text),
                f"{word_label}: invalid charStart/charEnd",
            )
            words.append(WordCue(word_text, word_start, word_end, char_start, char_end))
        sentences.append(SentenceCue(text, start, end, tuple(words)))
    return tuple(sentences)


def _validate_granularity(
    granularity: Any, sentences: tuple[SentenceCue, ...], *, where: str
) -> str:
    _require(granularity in GRANULARITIES, f"{where}: invalid granularity {granularity!r}")
    if granularity == GRANULARITY_SENTENCE:
        _require(
            all(not sentence.words for sentence in sentences),
            f"{where}: sentence-granularity transcripts must not contain word cues",
        )
    elif sentences:
        _require(
            any(sentence.words for sentence in sentences),
            f"{where}: word-granularity transcripts must contain word cues",
        )
    return granularity


def parse_chapter_file(data: Any, *, where: str = "chapter transcript") -> dict[str, Any]:
    """Validate a per-chapter workspace file; returns its normalized fields."""
    _require(isinstance(data, dict), f"{where}: root must be an object")
    _require(
        data.get("format") == CHAPTER_TRANSCRIPT_FORMAT,
        f"{where}: unexpected format {data.get('format')!r}",
    )
    _require(
        data.get("version") == TRANSCRIPT_VERSION,
        f"{where}: unsupported version {data.get('version')!r}",
    )
    chapter_id = data.get("chapterId")
    _require(isinstance(chapter_id, str) and bool(chapter_id), f"{where}: invalid chapterId")
    engine = data.get("engine")
    _require(isinstance(engine, str) and bool(engine), f"{where}: invalid engine")
    sentences = parse_sentences(data.get("sentences"), where=where)
    granularity = _validate_granularity(data.get("granularity"), sentences, where=where)
    return {
        "chapterId": chapter_id,
        "engine": engine,
        "granularity": granularity,
        "sentences": sentences,
    }


def parse_book_transcript(data: Any) -> BookTranscript:
    """Validate a combined transcript document (the embedded/sidecar form)."""
    where = "transcript"
    _require(isinstance(data, dict), f"{where}: root must be an object")
    _require(
        data.get("format") == TRANSCRIPT_FORMAT,
        f"{where}: unexpected format {data.get('format')!r}",
    )
    _require(
        data.get("version") == TRANSCRIPT_VERSION,
        f"{where}: unsupported version {data.get('version')!r}",
    )
    for key in ("producer", "engine", "generationKey", "language"):
        _require(isinstance(data.get(key), str), f"{where}: {key} must be a string")
    chapters_raw = data.get("chapters")
    _require(isinstance(chapters_raw, list), f"{where}: chapters must be a list")
    chapters: list[ChapterTranscript] = []
    previous_index = -1
    for position, entry in enumerate(chapters_raw):
        label = f"{where} chapter {position}"
        _require(isinstance(entry, dict), f"{label}: must be an object")
        index = entry.get("index")
        _require(type(index) is int and index > previous_index, f"{label}: invalid index")
        previous_index = index
        title = entry.get("title")
        _require(isinstance(title, str), f"{label}: title must be a string")
        sentences = parse_sentences(entry.get("sentences"), where=label)
        granularity = _validate_granularity(entry.get("granularity"), sentences, where=label)
        chapters.append(ChapterTranscript(index, title, granularity, sentences))
    return BookTranscript(
        producer=data["producer"],
        engine=data["engine"],
        generation_key=data["generationKey"],
        language=data["language"],
        chapters=tuple(chapters),
    )
