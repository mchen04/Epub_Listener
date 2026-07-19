import json
from pathlib import Path

import pytest

from epub_listener.domain.alignment import (
    RawWordCue,
    anchor_cues,
    build_chunk_sentences,
    build_sentence_cues,
    granularity_for,
    split_sentence_spans,
)
from epub_listener.domain.exceptions import TranscriptError
from epub_listener.domain.transcript import (
    BookTranscript,
    ChapterTranscript,
    SentenceCue,
    WordCue,
    book_transcript_to_dict,
    chapter_file_to_dict,
    parse_book_transcript,
    parse_chapter_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_word_fixture_parses() -> None:
    transcript = parse_book_transcript(load_fixture("valid-word.json"))
    assert transcript.engine == "edge"
    assert [chapter.index for chapter in transcript.chapters] == [0, 1]
    assert transcript.chapters[0].granularity == "word"
    first = transcript.chapters[0].sentences[0]
    assert first.text == "It was a dark and stormy night."
    assert first.words[0].text == "It"
    assert first.text[first.words[3].char_start : first.words[3].char_end] == "dark"


def test_valid_sentence_fixture_parses() -> None:
    transcript = parse_book_transcript(load_fixture("valid-sentence.json"))
    assert transcript.chapters[0].granularity == "sentence"
    assert all(not s.words for s in transcript.chapters[0].sentences)


def test_fixture_number_expansion_shares_char_range() -> None:
    transcript = parse_book_transcript(load_fixture("valid-word.json"))
    sentence = transcript.chapters[1].sentences[0]
    spoken = [w.text for w in sentence.words]
    assert spoken[:4] == ["Route", "one", "twenty", "three"]
    digits = {(w.char_start, w.char_end) for w in sentence.words[1:4]}
    # All three spoken words of "123" mark the same displayed token.
    assert len(digits) == 1
    char_start, char_end = digits.pop()
    assert sentence.text[char_start:char_end] == "123"


def test_round_trip_serialization() -> None:
    original = parse_book_transcript(load_fixture("valid-word.json"))
    again = parse_book_transcript(book_transcript_to_dict(original))
    assert again == original


def test_chapter_file_round_trip() -> None:
    sentences = (SentenceCue("Hello there.", 0, 900, (WordCue("Hello", 0, 400, 0, 5),)),)
    data = chapter_file_to_dict("0001", "kokoro", "word", sentences)
    parsed = parse_chapter_file(data)
    assert parsed["chapterId"] == "0001"
    assert parsed["sentences"] == sentences


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version=2),
        lambda d: d.update(format="something-else"),
        lambda d: d.pop("chapters"),
        lambda d: d["chapters"].append(dict(d["chapters"][0])),  # repeated index
        lambda d: d["chapters"][0]["sentences"][0].update(start=-5),
        lambda d: d["chapters"][0]["sentences"][0].update(text="   "),
        lambda d: d["chapters"][0]["sentences"][0]["words"][0].update(charEnd=10_000),
        lambda d: d["chapters"][0]["sentences"][0]["words"][0].update(start=400),
        lambda d: d["chapters"][0].update(granularity="chunk"),
    ],
)
def test_invalid_documents_rejected(mutate) -> None:
    document = load_fixture("valid-word.json")
    mutate(document)
    with pytest.raises(TranscriptError):
        parse_book_transcript(document)


def test_sentence_granularity_must_not_have_words() -> None:
    document = load_fixture("valid-word.json")
    document["chapters"][0]["granularity"] = "sentence"
    with pytest.raises(TranscriptError):
        parse_book_transcript(document)


def test_split_sentence_spans_prose_and_quotes() -> None:
    text = 'One sentence here. "Quoted!" she said.\nA newline break too'
    spans = [text[a:b] for a, b in split_sentence_spans(text)]
    assert spans == ["One sentence here.", '"Quoted!"', "she said.", "A newline break too"]


def test_split_sentence_spans_breaks_runaway_prose() -> None:
    text = "word " * 300  # no terminator at all
    spans = split_sentence_spans(text.strip())
    assert len(spans) > 1
    assert all(b - a <= 400 for a, b in spans)
    # Coverage: every non-space char is inside a span.
    covered = set()
    for a, b in spans:
        covered.update(range(a, b))
    stripped = text.strip()
    missing = [i for i, ch in enumerate(stripped) if not ch.isspace() and i not in covered]
    assert missing == []


def test_anchor_cues_expansion_and_case() -> None:
    text = "Chapter 12 begins."
    cues = [
        RawWordCue("chapter", 0, 200),
        RawWordCue("twelve", 210, 400),
        RawWordCue("begins", 410, 600),
    ]
    anchored = anchor_cues(text, cues)
    assert (anchored[0].char_start, anchored[0].char_end) == (0, 7)
    # "twelve" has no textual match: it marks the written token being spoken.
    assert text[anchored[1].char_start : anchored[1].char_end] == "12"
    assert (anchored[2].char_start, anchored[2].char_end) == (11, 17)


def test_build_sentence_cues_orders_and_contains_words() -> None:
    text = "First one here. Second one there."
    cues = [
        RawWordCue("First", 100, 200),
        RawWordCue("one", 210, 300),
        RawWordCue("here", 310, 400),
        RawWordCue("Second", 500, 600),
        RawWordCue("one", 610, 700),
        RawWordCue("there", 710, 800),
    ]
    sentences = build_sentence_cues(text, cues)
    assert [s.text for s in sentences] == ["First one here.", "Second one there."]
    assert sentences[0].start_ms == 100 and sentences[0].end_ms == 400
    assert sentences[1].start_ms == 500
    assert [w.text for w in sentences[1].words] == ["Second", "one", "there"]
    # The duplicate word "one" anchors inside its own sentence both times.
    second = sentences[1]
    assert second.text[second.words[1].char_start : second.words[1].char_end] == "one"


def test_build_chunk_sentences_fallback() -> None:
    sentences = build_chunk_sentences(
        [("First chunk.", 0, 1000), (" ", 1000, 1100), ("Next.", 1050, 2000)]
    )
    assert [s.text for s in sentences] == ["First chunk.", "Next."]
    assert sentences[1].start_ms == 1050
    assert granularity_for(sentences) == "sentence"
    assert granularity_for([SentenceCue("Hi.", 0, 100, (WordCue("Hi", 0, 100, 0, 2),))]) == "word"


def test_full_document_validation_via_dataclasses() -> None:
    sentences = (SentenceCue("Only sentence.", 0, 500, ()),)
    document = book_transcript_to_dict(
        BookTranscript(
            producer="epub_listener/test",
            engine="kokoro",
            generation_key="k",
            language="en",
            chapters=(ChapterTranscript(0, "T", "sentence", sentences),),
        )
    )
    assert parse_book_transcript(document).chapters[0].sentences == sentences
