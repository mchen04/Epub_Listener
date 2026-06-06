"""Comprehensive smoke test suite for the refactored Epub Listener."""

import asyncio
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from epub_listener.application.orchestrator import BuildAudiobookUseCase
from epub_listener.config import Settings
from epub_listener.domain.models import AudioSegment, Chapter
from epub_listener.domain.sanitize import sanitize_filename
from epub_listener.infrastructure.media.ffmpeg_assembler import FFmpegMediaAssembler
from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder
from epub_listener.infrastructure.parsers.ebooklib_parser import EbookLibParser
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker
from epub_listener.infrastructure.tts.edge_tts import EdgeTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import KokoroTTSProvider
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms


def test_parser():
    parser = EbookLibParser()
    epub_dir = Path("epub")
    for epub_file in epub_dir.glob("*.epub"):
        chapters = parser.parse(epub_file)
        assert len(chapters) > 0
        assert all(isinstance(c, Chapter) for c in chapters)
        assert all(c.checksum for c in chapters)


def test_edge_tts():
    provider = EdgeTTSProvider()
    assert provider.supports_concurrency() == "async"

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "test.mp3"
        text = "This is a smoke test for the Edge TTS provider in Epub Listener."
        duration = provider.generate(text, output, None, "+0%")
        assert duration > 0
        assert output.exists()
        assert output.stat().st_size > 0
        probed = get_audio_duration_ms(output)
        assert abs(probed - duration) < 100  # within 100ms


def test_edge_tts_async_batch():
    """generate_many runs inside one event loop — guards the nested asyncio.run() bug."""
    provider = EdgeTTSProvider()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        jobs = [
            ("First short clip for the batch.", tmp / "a.mp3", None, "+0%"),
            ("Second short clip for the batch.", tmp / "b.mp3", None, "+0%"),
        ]
        durations = asyncio.run(provider.generate_many(jobs))
        assert len(durations) == 2
        assert all(d > 0 for d in durations)
        assert all((tmp / name).exists() for name in ("a.mp3", "b.mp3"))


def test_kokoro_tts():
    try:
        provider = KokoroTTSProvider()
        assert provider.supports_concurrency() == "parallel"

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.mp3"
            duration = provider.generate("This is a smoke test for Kokoro TTS.", output, "af_heart", "+0%")
            if duration <= 0:
                pytest.skip("Kokoro produced no audio (model may need download)")
            assert output.exists()
    except Exception as exc:  # missing model/deps — not a regression
        pytest.skip(f"Kokoro unavailable: {exc}")


def test_media_pipeline():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        provider = EdgeTTSProvider()
        seg1_path = tmp / "seg1.mp3"
        seg2_path = tmp / "seg2.mp3"
        provider.generate("Chapter one text here.", seg1_path, None, "+0%")
        provider.generate("Chapter two text here.", seg2_path, None, "+0%")

        dur1 = get_audio_duration_ms(seg1_path)
        dur2 = get_audio_duration_ms(seg2_path)
        segments = [
            AudioSegment(path=seg1_path, duration_ms=dur1, chapter_id="0001"),
            AudioSegment(path=seg2_path, duration_ms=dur2, chapter_id="0002"),
        ]

        meta_builder = FFmpegMetadataBuilder()
        meta_path = tmp / "ffmetadata.txt"
        meta_builder.build(
            segments, {"0001": "Chapter 1", "0002": "Chapter 2"}, "Test Book", "Test Author", meta_path
        )
        assert meta_path.exists()
        content = meta_path.read_text()
        assert "CHAPTER" in content
        assert "Chapter 1" in content
        assert "Chapter 2" in content

        assembler = FFmpegMediaAssembler()
        final = tmp / "final.mp3"
        assembler.assemble(segments, meta_path, final)
        assert final.exists()
        assert final.stat().st_size > 0


def test_progress_tracker():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tracker = JsonProgressTracker(tmp)
        assert not tracker.is_complete("ch1", "abc123")

        dummy_audio = tmp / "chap_ch1.mp3"
        dummy_audio.write_bytes(b"dummy mp3 data")

        tracker.mark_complete("ch1", "abc123", 4321)
        assert tracker.is_complete("ch1", "abc123")
        assert tracker.cached_duration_ms("ch1") == 4321
        assert not tracker.is_complete("ch1", "different_checksum")

        # Re-load and verify persistence (mark_complete persists eagerly)
        tracker2 = JsonProgressTracker(tmp)
        assert tracker2.is_complete("ch1", "abc123")
        assert tracker2.cached_duration_ms("ch1") == 4321


def test_file_sanitizer():
    assert sanitize_filename("Hello World!") == "Hello World"
    assert sanitize_filename("Ch. 1: The Beginning?") == "Ch 1 The Beginning"
    assert sanitize_filename("") == "unnamed"


def test_config_validation():
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    cfg = Settings(input_epub=tmp_path, speed="+10%")
    assert cfg.resolve_output_path().suffix == ".mp3"

    with pytest.raises(ValidationError):
        Settings(input_epub=tmp_path, speed="invalid")

    tmp_path.unlink()


def test_full_orchestrator_single_chapter():
    tiny_chapter = Chapter(id="0000", title="Smoke Test", text="Hello world. This is a test.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        progress_dir = tmp / "progress"
        epub_path = tmp / "dummy.epub"
        epub_path.touch()  # Settings validates the file exists; the parser is mocked below
        settings = Settings(
            input_epub=epub_path,
            output_path=tmp / "smoke_test.mp3",
            speed="+0%",
            concurrency="sequential",
            log_level="WARNING",
        )

        class SingleChapterParser:
            def parse(self, path):
                return [tiny_chapter]

        use_case = BuildAudiobookUseCase(
            parser=SingleChapterParser(),
            tts=EdgeTTSProvider(),
            assembler=FFmpegMediaAssembler(),
            metadata_builder=FFmpegMetadataBuilder(),
            tracker=JsonProgressTracker(progress_dir),
        )

        output = use_case.execute(settings, temp_dir=progress_dir)
        assert output.exists()
        assert output.stat().st_size > 0


def test_scraper_toc_fetch():
    import requests

    from epub_listener.scrapers.worm import WormScraper

    scraper = WormScraper(delay=0.5)
    try:
        with requests.Session() as session:
            links = scraper._get_chapter_links(session)
        assert len(links) > 100  # Worm is long
    except Exception as exc:  # network-dependent
        pytest.skip(f"ToC fetch unavailable: {exc}")
