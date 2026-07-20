import asyncio
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from epub_listener.application.ports import TTSJob, transcript_path_for
from epub_listener.domain.models import AudioSegment
from epub_listener.domain.transcript import (
    GEOB_DESCRIPTION,
    SentenceCue,
    WordCue,
    parse_book_transcript,
    parse_chapter_file,
)
from epub_listener.infrastructure.media.transcript_embedder import Id3TranscriptEmbedder
from epub_listener.infrastructure.tts import edge_tts as edge_tts_module
from epub_listener.infrastructure.tts import kokoro_tts
from epub_listener.infrastructure.tts.edge_tts import EdgeTTSProvider
from epub_listener.infrastructure.tts.transcript_capture import (
    KokoroTokenWalker,
    write_chapter_transcript,
)
from tests.integration.smoke_test import _write_tone_mp3  # noqa: E402


def _load_chapter_file(path: Path) -> dict:
    return parse_chapter_file(json.loads(path.read_text(encoding="utf-8")))


class FakeStreamCommunicate:
    """Mimics edge_tts.Communicate.stream(): audio chunks + WordBoundary events."""

    events: list[dict] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def stream(self):
        for event in type(self).events:
            yield event


def _tick(ms: int) -> int:
    return ms * 10_000


def test_edge_provider_captures_word_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeStreamCommunicate.events = [
        {"type": "audio", "data": b"mp3-bytes"},
        {"type": "WordBoundary", "offset": _tick(0), "duration": _tick(210), "text": "Hello"},
        {"type": "audio", "data": b"more"},
        {"type": "WordBoundary", "offset": _tick(250), "duration": _tick(200), "text": "there"},
        {"type": "WordBoundary", "offset": _tick(500), "duration": _tick(300), "text": "friend"},
    ]
    monkeypatch.setattr(edge_tts_module.edge_tts, "Communicate", FakeStreamCommunicate)
    monkeypatch.setattr(edge_tts_module, "commit_generated_mp3", lambda tmp, out: 1000)

    output = tmp_path / "chapter.mp3"
    job = TTSJob(
        "0000",
        "Hello there, friend.",
        output,
        None,
        "+0%",
        transcript_path=transcript_path_for(output),
    )
    assert EdgeTTSProvider().run_job(job) == 1000

    parsed = _load_chapter_file(transcript_path_for(output))
    assert parsed["engine"] == "edge"
    assert parsed["granularity"] == "word"
    (sentence,) = parsed["sentences"]
    assert sentence.text == "Hello there, friend."
    assert [w.text for w in sentence.words] == ["Hello", "there", "friend"]
    # Offsets carry the empirically measured +100ms boundary-lead correction.
    assert [w.start_ms for w in sentence.words] == [100, 350, 600]
    assert sentence.text[sentence.words[2].char_start : sentence.words[2].char_end] == "friend"


def test_edge_provider_skips_transcript_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeStreamCommunicate.events = [
        {"type": "audio", "data": b"mp3-bytes"},
        {"type": "WordBoundary", "offset": 0, "duration": _tick(100), "text": "Hello"},
    ]
    monkeypatch.setattr(edge_tts_module.edge_tts, "Communicate", FakeStreamCommunicate)
    monkeypatch.setattr(edge_tts_module, "commit_generated_mp3", lambda tmp, out: 1000)

    output = tmp_path / "chapter.mp3"
    assert EdgeTTSProvider().generate("Hello.", output, None, "+0%") == 1000
    assert not transcript_path_for(output).exists()


def _fake_kokoro_result(text: str, tokens: list | None, seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        graphemes=text,
        tokens=tokens,
        audio=[0.0] * int(seconds * kokoro_tts.SAMPLE_RATE),
    )


def _token(text: str, start_ts: float | None, end_ts: float | None) -> SimpleNamespace:
    return SimpleNamespace(text=text, start_ts=start_ts, end_ts=end_ts)


def _stub_kokoro_io(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSoundFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSoundFile":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def write(self, samples: object) -> None:
            return None

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(SoundFile=FakeSoundFile))
    monkeypatch.setattr(kokoro_tts, "run_ffmpeg", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        kokoro_tts, "commit_generated_mp3", lambda tmp, out, should_cancel=None: 2000
    )


def test_kokoro_provider_captures_token_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_kokoro_io(monkeypatch)
    text = "Hello there.\nSecond chunk."
    results = [
        _fake_kokoro_result(
            "Hello there.",
            [
                _token("Hello", 0.0, 0.4),
                _token("there", 0.5, 0.9),
                _token(".", None, None),
            ],
            2.0,
        ),
        _fake_kokoro_result(
            "Second chunk.",
            [
                _token("Second", 0.05, 0.5),
                _token("chunk", 0.6, 1.0),
                _token(".", None, None),
            ],
            1.5,
        ),
    ]
    monkeypatch.setattr(kokoro_tts, "_get_pipeline", lambda lang: lambda *args, **kwargs: results)

    output = tmp_path / "chapter.mp3"
    job = TTSJob(
        "0002", text, output, "af_heart", "+0%", transcript_path=transcript_path_for(output)
    )
    assert kokoro_tts.KokoroTTSProvider().run_job(job) == 2000

    parsed = _load_chapter_file(transcript_path_for(output))
    assert parsed["granularity"] == "word"
    sentences = parsed["sentences"]
    assert [s.text for s in sentences] == ["Hello there.", "Second chunk."]
    # Second chunk's cues are offset by the first chunk's audio length (2s).
    assert sentences[1].words[0].start_ms == 2000 + 50
    assert sentences[1].text[sentences[1].words[1].char_start :][:5] == "chunk"


def test_kokoro_provider_falls_back_to_chunks_without_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_kokoro_io(monkeypatch)
    results = [
        _fake_kokoro_result("First chunk text.", None, 1.0),
        _fake_kokoro_result("Second chunk text.", None, 1.0),
    ]
    monkeypatch.setattr(kokoro_tts, "_get_pipeline", lambda lang: lambda *args, **kwargs: results)

    output = tmp_path / "chapter.mp3"
    job = TTSJob(
        "0003",
        "First chunk text.\nSecond chunk text.",
        output,
        "af_heart",
        "+0%",
        transcript_path=transcript_path_for(output),
    )
    kokoro_tts.KokoroTTSProvider().run_job(job)

    parsed = _load_chapter_file(transcript_path_for(output))
    assert parsed["granularity"] == "sentence"
    assert [s.text for s in parsed["sentences"]] == ["First chunk text.", "Second chunk text."]
    assert parsed["sentences"][1].start_ms == 1000


def test_kokoro_token_walker_anchors_with_windowed_recovery() -> None:
    walker = KokoroTokenWalker("Alpha beta gamma")
    cues = walker.cues_for_chunk(
        [
            _token("Alpha", 0.0, 0.2),
            _token("mismatch", 0.2, 0.3),
            _token("gamma", 0.4, 0.6),
        ],
        chunk_start_ms=100,
    )
    assert (cues[0].char_start, cues[0].char_end) == (0, 5)
    assert cues[0].start_ms == 100
    assert cues[1].char_start is None
    assert (cues[2].char_start, cues[2].char_end) == (11, 16)


def test_embedder_adds_frame_and_preserves_chapters(tmp_path: Path) -> None:
    from epub_listener.infrastructure.media.ffmpeg_assembler import FFmpegMediaAssembler
    from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder
    from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms

    segments = []
    titles = {}
    for index in range(2):
        chapter_id = f"{index:04d}"
        audio = tmp_path / f"chap_{chapter_id}.mp3"
        _write_tone_mp3(audio, 440 + index * 110)
        write_chapter_transcript(
            transcript_path_for(audio),
            chapter_id,
            "fake",
            [
                SentenceCue(
                    f"Sentence {index}.",
                    0,
                    900,
                    (WordCue("Sentence", 0, 400, 0, 8),),
                )
            ],
        )
        segments.append(
            AudioSegment(
                path=audio, duration_ms=get_audio_duration_ms(audio), chapter_id=chapter_id
            )
        )
        titles[chapter_id] = f"Chapter {index + 1}"

    metadata = tmp_path / "ffmetadata.txt"
    FFmpegMetadataBuilder().build(segments, titles, "Book", "Author", metadata)
    output = tmp_path / "book.mp3"
    FFmpegMediaAssembler().assemble(segments, metadata, output)

    assert Id3TranscriptEmbedder().embed(segments, titles, "fake", "key", output) is True

    from mutagen.id3 import ID3

    tags = ID3(output)
    frames = [frame for frame in tags.getall("GEOB") if frame.desc == GEOB_DESCRIPTION]
    assert len(frames) == 1
    document = json.loads(gzip.decompress(frames[0].data).decode("utf-8"))
    transcript = parse_book_transcript(document)
    assert [chapter.index for chapter in transcript.chapters] == [0, 1]
    assert [chapter.title for chapter in transcript.chapters] == ["Chapter 1", "Chapter 2"]

    sidecar = output.with_suffix(".transcript.json")
    assert json.loads(sidecar.read_text(encoding="utf-8")) == document

    # Chapters survive the tag rewrite.
    from tests.integration.smoke_test import _probe_chapters

    assert [chapter["tags"]["title"] for chapter in _probe_chapters(output)] == [
        "Chapter 1",
        "Chapter 2",
    ]


def test_embedder_skips_when_chapter_transcript_missing(tmp_path: Path) -> None:
    audio = tmp_path / "chap_0000.mp3"
    _write_tone_mp3(audio, 440)
    segments = [AudioSegment(path=audio, duration_ms=1000, chapter_id="0000")]
    output = tmp_path / "book.mp3"
    output.write_bytes(audio.read_bytes())

    assert Id3TranscriptEmbedder().embed(segments, {}, "fake", "key", output) is False
    assert not output.with_suffix(".transcript.json").exists()


@pytest.mark.parametrize(
    "corrupt",
    [b"\xff\xfe\x00not utf-8", b"{ not valid json", b'{"format":"wrong"}'],
    ids=["undecodable", "bad-json", "schema-invalid"],
)
def test_embedder_never_breaks_the_build_on_corrupt_chapter(tmp_path: Path, corrupt: bytes) -> None:
    """A corrupt chapter transcript must skip embedding, not raise (the audio
    is already written by the time embed() runs)."""
    audio = tmp_path / "chap_0000.mp3"
    _write_tone_mp3(audio, 440)
    transcript_path_for(audio).write_bytes(corrupt)
    segments = [AudioSegment(path=audio, duration_ms=1000, chapter_id="0000")]
    output = tmp_path / "book.mp3"
    output.write_bytes(audio.read_bytes())

    assert Id3TranscriptEmbedder().embed(segments, {}, "fake", "key", output) is False
    assert not output.with_suffix(".transcript.json").exists()


def test_embedder_never_breaks_the_build_on_tagging_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mutagen/tagging failure must be contained, not raised."""
    from epub_listener.infrastructure.media import transcript_embedder as embedder_module

    audio = tmp_path / "chap_0000.mp3"
    _write_tone_mp3(audio, 440)
    write_chapter_transcript(
        transcript_path_for(audio),
        "0000",
        "fake",
        [SentenceCue("Hello there.", 0, 900, (WordCue("Hello", 0, 400, 0, 5),))],
    )
    segments = [AudioSegment(path=audio, duration_ms=1000, chapter_id="0000")]
    output = tmp_path / "book.mp3"
    output.write_bytes(audio.read_bytes())

    def boom(self: object, out: Path, payload: str) -> None:
        raise RuntimeError("tagging blew up")

    monkeypatch.setattr(embedder_module.Id3TranscriptEmbedder, "_write_frame", boom)

    assert Id3TranscriptEmbedder().embed(segments, {}, "fake", "key", output) is False


def test_edge_stream_timeout_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class StallingCommunicate:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def stream(self):
            await asyncio.sleep(60)
            yield {"type": "audio", "data": b""}

    monkeypatch.setattr(edge_tts_module.edge_tts, "Communicate", StallingCommunicate)
    from epub_listener.domain.exceptions import TTSGenerationError

    with pytest.raises(TTSGenerationError, match="timed out"):
        EdgeTTSProvider(timeout_seconds=0.05).generate("text", tmp_path / "c.mp3", None, "+0%")
