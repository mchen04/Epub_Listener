"""Comprehensive smoke test suite for the refactored Epub Listener."""

import json
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from ebooklib import epub
from pydantic import ValidationError

from epub_listener.application.commands import BuildAudiobookCommand
from epub_listener.application.orchestrator import BuildAudiobookUseCase
from epub_listener.application.ports import GenerationCallback, TTSJob, TTSResult
from epub_listener.config import Settings
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.domain.models import AudioSegment, Chapter
from epub_listener.domain.sanitize import sanitize_filename
from epub_listener.infrastructure.media.ffmpeg_assembler import FFmpegMediaAssembler
from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder
from epub_listener.infrastructure.parsers.ebooklib_parser import EbookLibParser
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker
from epub_listener.infrastructure.tts.batch import SequentialTTSBatchGenerator
from epub_listener.infrastructure.tts.edge_tts import EdgeAsyncTTSBatchGenerator, EdgeTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import (
    KokoroParallelTTSBatchGenerator,
    KokoroTTSProvider,
)
from epub_listener.infrastructure.tts.mlx_kokoro_tts import KokoroMLXTTSProvider
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg


def _write_epub_fixture(path: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("parser-smoke-fixture")
    book.set_title("Parser Smoke Fixture")
    book.set_language("en")
    book.add_author("Epub Listener")

    chapter = epub.EpubHtml(title="Chapter 1", file_name="chapter_1.xhtml", lang="en")
    chapter.content = (
        "<html><body><h1>Chapter 1</h1>"
        "<p>This fixture contains enough chapter text for the parser smoke test "
        "to exercise real EPUB document extraction instead of depending on "
        "local, untracked books.</p></body></html>"
    )

    book.add_item(chapter)
    book.toc = (epub.Link("chapter_1.xhtml", "Chapter 1", "chapter-1"),)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(path, book)


def test_parser(tmp_path: Path) -> None:
    epub_path = tmp_path / "fixture.epub"
    _write_epub_fixture(epub_path)

    parser = EbookLibParser()
    chapters = parser.parse(epub_path)

    assert len(chapters) == 1
    assert all(isinstance(c, Chapter) for c in chapters)
    assert all(c.checksum for c in chapters)
    chapter = chapters[0]
    assert chapter.id == "0000"
    assert chapter.title == "Chapter 1"
    assert chapter.text.startswith("Chapter 1")
    assert "local, untracked books" in chapter.text
    assert "<p>" not in chapter.text
    assert "parser-smoke-fixture" not in chapter.text


@pytest.mark.live
def test_edge_tts() -> None:
    provider = EdgeTTSProvider()

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "test.mp3"
        text = "This is a smoke test for the Edge TTS provider in Epub Listener."
        duration = provider.generate(text, output, None, "+0%")
        assert duration > 0
        assert output.exists()
        assert output.stat().st_size > 0
        probed = get_audio_duration_ms(output)
        assert abs(probed - duration) < 100  # within 100ms


@pytest.mark.live
def test_edge_tts_async_batch() -> None:
    provider = EdgeTTSProvider()
    batch = EdgeAsyncTTSBatchGenerator(provider)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        jobs = [
            TTSJob("a", "First short clip for the batch.", tmp_dir / "a.mp3", None, "+0%"),
            TTSJob("b", "Second short clip for the batch.", tmp_dir / "b.mp3", None, "+0%"),
        ]
        results: list[TTSResult] = []
        batch.generate_many(jobs, results.append)
        assert len(results) == 2
        assert all(result.duration_ms > 0 for result in results)
        assert all((tmp_dir / name).exists() for name in ("a.mp3", "b.mp3"))


@pytest.mark.live
def test_kokoro_tts() -> None:
    provider = KokoroTTSProvider()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.mp3"
            duration = provider.generate(
                "This is a smoke test for Kokoro TTS.", output, "af_heart", "+0%"
            )
            probed = get_audio_duration_ms(output)
            assert duration > 0
            assert output.exists()
            assert abs(probed - duration) < 100
    except TTSGenerationError as exc:
        if "Kokoro is not installed" in str(exc) or "soundfile not installed" in str(exc):
            pytest.skip(f"Kokoro unavailable: {exc}")
        raise


@pytest.mark.live
def test_kokoro_hybrid_mps_batch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        jobs = [
            TTSJob(
                "mps-or-cpu-a",
                "A short hybrid Kokoro test for the first worker.",
                tmp_dir / "a.mp3",
                "af_heart",
                "+0%",
            ),
            TTSJob(
                "mps-or-cpu-b",
                "A second short hybrid Kokoro test for the other worker.",
                tmp_dir / "b.mp3",
                "af_heart",
                "+0%",
            ),
        ]
        results: list[TTSResult] = []
        KokoroParallelTTSBatchGenerator(
            max_workers=8,
            hybrid_mps=True,
        ).generate_many(jobs, results.append)

        assert len(results) == 2
        assert all(result.duration_ms > 0 for result in results)
        assert all((tmp_dir / name).stat().st_size > 0 for name in ("a.mp3", "b.mp3"))


@pytest.mark.live
def test_kokoro_mlx_tts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "mlx.mp3"
        duration = KokoroMLXTTSProvider().generate(
            "This is a smoke test for Kokoro through Apple MLX.",
            output,
            "af_heart",
            "+0%",
        )

        assert duration > 0
        assert output.stat().st_size > 0
        assert abs(get_audio_duration_ms(output) - duration) < 100


def _write_tone_mp3(output: Path, frequency: int) -> None:
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration=0.15",
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "9",
        output,
        timeout=30,
    )


def _probe_chapters(path: Path) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_chapters",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    data = json.loads(result.stdout)
    chapters = data["chapters"]
    assert isinstance(chapters, list)
    return chapters


def _chapter_time_ms(chapter: dict[str, Any], key: str) -> int:
    return round(float(str(chapter[f"{key}_time"])) * 1000)


def _assert_ms_close(actual: int, expected: int, tolerance_ms: int = 80) -> None:
    assert abs(actual - expected) <= tolerance_ms


def test_media_pipeline_with_local_segments(tmp_path: Path) -> None:
    segment_dir = tmp_path / "reader's cache"
    segment_dir.mkdir()
    seg1_path = segment_dir / "seg1.mp3"
    seg2_path = segment_dir / "seg2.mp3"
    _write_tone_mp3(seg1_path, 440)
    _write_tone_mp3(seg2_path, 660)

    dur1 = get_audio_duration_ms(seg1_path)
    dur2 = get_audio_duration_ms(seg2_path)
    segments = [
        AudioSegment(path=seg1_path, duration_ms=dur1, chapter_id="0001"),
        AudioSegment(path=seg2_path, duration_ms=dur2, chapter_id="0002"),
    ]

    meta_path = tmp_path / "ffmetadata.txt"
    FFmpegMetadataBuilder().build(
        segments,
        {"0001": "Chapter 1", "0002": "Chapter 2"},
        "Local Media Smoke",
        "Test Author",
        meta_path,
    )

    final = tmp_path / "final.mp3"
    FFmpegMediaAssembler().assemble(segments, meta_path, final)

    assert final.exists()
    assert final.stat().st_size > 0
    final_duration = get_audio_duration_ms(final)
    assert final_duration > 0
    # Independently encoded MP3 segments contain per-file encoder padding.
    # The assembler must remove it rather than accumulating timing drift at
    # every chapter join.
    _assert_ms_close(final_duration, dur1 + dur2, tolerance_ms=30)
    chapters = _probe_chapters(final)
    assert [chapter["tags"]["title"] for chapter in chapters] == ["Chapter 1", "Chapter 2"]
    _assert_ms_close(_chapter_time_ms(chapters[0], "start"), 0)
    _assert_ms_close(_chapter_time_ms(chapters[0], "end"), dur1)
    _assert_ms_close(_chapter_time_ms(chapters[1], "start"), dur1)
    _assert_ms_close(_chapter_time_ms(chapters[1], "end"), dur1 + dur2)


@pytest.mark.live
def test_media_pipeline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        provider = EdgeTTSProvider()
        seg1_path = tmp_dir / "seg1.mp3"
        seg2_path = tmp_dir / "seg2.mp3"
        provider.generate("Chapter one text here.", seg1_path, None, "+0%")
        provider.generate("Chapter two text here.", seg2_path, None, "+0%")

        dur1 = get_audio_duration_ms(seg1_path)
        dur2 = get_audio_duration_ms(seg2_path)
        segments = [
            AudioSegment(path=seg1_path, duration_ms=dur1, chapter_id="0001"),
            AudioSegment(path=seg2_path, duration_ms=dur2, chapter_id="0002"),
        ]

        meta_builder = FFmpegMetadataBuilder()
        meta_path = tmp_dir / "ffmetadata.txt"
        meta_builder.build(
            segments,
            {"0001": "Chapter 1", "0002": "Chapter 2"},
            "Test Book",
            "Test Author",
            meta_path,
        )
        assert meta_path.exists()
        content = meta_path.read_text()
        assert "CHAPTER" in content
        assert "Chapter 1" in content
        assert "Chapter 2" in content

        assembler = FFmpegMediaAssembler()
        final = tmp_dir / "final.mp3"
        assembler.assemble(segments, meta_path, final)
        assert final.exists()
        assert final.stat().st_size > 0


def test_progress_tracker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tracker = JsonProgressTracker(tmp_dir)
        assert not tracker.is_complete("ch1", "abc123")

        dummy_audio = tmp_dir / "chap_ch1.mp3"
        dummy_audio.write_bytes(b"dummy mp3 data")

        tracker.mark_complete("ch1", "abc123", 4321)
        assert tracker.is_complete("ch1", "abc123")
        assert tracker.cached_duration_ms("ch1") == 4321
        assert not tracker.is_complete("ch1", "different_checksum")

        # Re-load and verify persistence (mark_complete persists eagerly)
        tracker2 = JsonProgressTracker(tmp_dir)
        assert tracker2.is_complete("ch1", "abc123")
        assert tracker2.cached_duration_ms("ch1") == 4321


def test_file_sanitizer() -> None:
    assert sanitize_filename("Hello World!") == "Hello World"
    assert sanitize_filename("Ch. 1: The Beginning?") == "Ch 1 The Beginning"
    assert sanitize_filename("") == "unnamed"


def test_config_validation() -> None:
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    cfg = Settings(input_epub=tmp_path, speed="+10%")
    assert cfg.concurrency == "auto"
    assert cfg.resolve_output_path().suffix == ".mp3"

    with pytest.raises(ValidationError):
        Settings(input_epub=tmp_path, speed="invalid")

    tmp_path.unlink()


class StaticChapterParser:
    def __init__(self, chapters: list[Chapter]) -> None:
        self._chapters = chapters

    def parse(self, path: Path) -> list[Chapter]:
        return self._chapters


class LocalToneBatchGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        self.calls += 1
        for index, job in enumerate(jobs):
            _write_tone_mp3(job.output, 440 + index * 110)
            on_complete(TTSResult(job.chapter_id, get_audio_duration_ms(job.output)))


class FailingBatchGenerator:
    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        raise AssertionError("cached chapters should not be regenerated")


def test_full_orchestrator_pipeline_with_local_audio_and_resume(tmp_path: Path) -> None:
    chapters = [
        Chapter(id="0000", title="Local One", text="one " * 50),
        Chapter(id="0001", title="Local Two", text="two " * 50),
    ]
    epub_path = tmp_path / "dummy.epub"
    epub_path.touch()
    progress_dir = tmp_path / "progress"
    tts = LocalToneBatchGenerator()

    use_case = BuildAudiobookUseCase(
        parser=StaticChapterParser(chapters),
        tts=tts,
        assembler=FFmpegMediaAssembler(),
        metadata_builder=FFmpegMetadataBuilder(),
        tracker=JsonProgressTracker(progress_dir),
    )
    command = BuildAudiobookCommand(
        input_epub=epub_path,
        output_path=tmp_path / "first.mp3",
        author="Author",
        voice=None,
        speed="+0%",
        temp_dir=progress_dir,
    )

    first_output = use_case.execute(command)

    assert first_output.exists()
    assert tts.calls == 1
    assert [chapter["tags"]["title"] for chapter in _probe_chapters(first_output)] == [
        "Local One",
        "Local Two",
    ]

    resumed = BuildAudiobookUseCase(
        parser=StaticChapterParser(chapters),
        tts=FailingBatchGenerator(),
        assembler=FFmpegMediaAssembler(),
        metadata_builder=FFmpegMetadataBuilder(),
        tracker=JsonProgressTracker(progress_dir),
    )
    resumed_command = BuildAudiobookCommand(
        input_epub=epub_path,
        output_path=tmp_path / "resumed.mp3",
        author="Author",
        voice=None,
        speed="+0%",
        temp_dir=progress_dir,
    )

    resumed_output = resumed.execute(resumed_command)

    assert resumed_output.exists()
    assert [chapter["tags"]["title"] for chapter in _probe_chapters(resumed_output)] == [
        "Local One",
        "Local Two",
    ]


@pytest.mark.live
def test_full_orchestrator_single_chapter() -> None:
    tiny_chapter = Chapter(id="0000", title="Smoke Test", text="Hello world. This is a test.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        progress_dir = tmp_dir / "progress"
        epub_path = tmp_dir / "dummy.epub"
        epub_path.touch()  # Settings validates the file exists; the parser is mocked below
        settings = Settings(
            input_epub=epub_path,
            output_path=tmp_dir / "smoke_test.mp3",
            speed="+0%",
            concurrency="sequential",
            log_level="WARNING",
        )

        class SingleChapterParser:
            def parse(self, path: Path) -> list[Chapter]:
                return [tiny_chapter]

        use_case = BuildAudiobookUseCase(
            parser=SingleChapterParser(),
            tts=SequentialTTSBatchGenerator(EdgeTTSProvider()),
            assembler=FFmpegMediaAssembler(),
            metadata_builder=FFmpegMetadataBuilder(),
            tracker=JsonProgressTracker(progress_dir),
        )

        command = BuildAudiobookCommand(
            input_epub=settings.input_epub,
            output_path=settings.resolve_output_path(),
            author=settings.author,
            voice=settings.resolved_voice,
            speed=settings.speed,
            temp_dir=progress_dir,
        )

        output = use_case.execute(command)
        assert output.exists()
        assert output.stat().st_size > 0


@pytest.mark.live
def test_scraper_toc_fetch() -> None:
    import requests

    from epub_listener.scrapers.worm import ScrapeError, WormScraper

    scraper = WormScraper(delay=0.5)
    try:
        with requests.Session() as session:
            links = scraper._get_chapter_links(session)
    except ScrapeError as exc:
        if isinstance(exc.__cause__, requests.RequestException):
            pytest.skip(f"ToC fetch unavailable: {exc}")
        raise
    assert len(links) > 100  # Worm is long
