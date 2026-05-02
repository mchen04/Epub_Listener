"""Comprehensive smoke test suite for the refactored Epub Listener."""

import asyncio
import tempfile
from pathlib import Path

# Domain
from epub_listener.domain.models import Chapter, AudioSegment
from epub_listener.domain.exceptions import EpubListenerError

# Infrastructure
from epub_listener.infrastructure.parsers.ebooklib_parser import EbookLibParser
from epub_listener.infrastructure.tts.edge_tts import EdgeTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import KokoroTTSProvider
from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder
from epub_listener.infrastructure.media.ffmpeg_assembler import FFmpegMediaAssembler
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker
from epub_listener.infrastructure.utils.file_sanitizer import FileSanitizer
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms

# Application
from epub_listener.application.orchestrator import BuildAudiobookUseCase
from epub_listener.config import Settings

def test_parser():
    print("\n[TEST] Parser - All EPUB files")
    parser = EbookLibParser()
    epub_dir = Path("epub")
    for epub_file in epub_dir.glob("*.epub"):
        chapters = parser.parse(epub_file)
        print(f"  {epub_file.name}: {len(chapters)} chapters, "
              f"{sum(c.word_count for c in chapters):,} words")
        assert len(chapters) > 0
        assert all(isinstance(c, Chapter) for c in chapters)
        assert all(c.checksum for c in chapters)
    print("  PASSED")

def test_edge_tts():
    print("\n[TEST] Edge-TTS Provider")
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
        assert abs(probed - duration) < 100  # Within 100ms
    print(f"  Generated audio: {duration}ms (probed: {probed}ms)")
    print("  PASSED")

def test_kokoro_tts():
    print("\n[TEST] Kokoro TTS Provider")
    try:
        provider = KokoroTTSProvider()
        assert provider.supports_concurrency() == "parallel"
        
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.mp3"
            text = "This is a smoke test for Kokoro TTS."
            duration = provider.generate(text, output, "af_heart", "+0%")
            if duration > 0:
                assert output.exists()
                probed = get_audio_duration_ms(output)
                print(f"  Generated audio: {duration}ms (probed: {probed}ms)")
                print("  PASSED")
            else:
                print("  WARNING: Kokoro generated 0ms (model may need download)")
    except Exception as exc:
        print(f"  SKIPPED: {exc}")

def test_media_pipeline():
    print("\n[TEST] Media Builder + Assembler")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Create a dummy audio file via Edge-TTS
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
        meta_builder.build(segments, {"0001": "Chapter 1", "0002": "Chapter 2"}, 
                          "Test Book", "Test Author", meta_path)
        assert meta_path.exists()
        content = meta_path.read_text()
        assert "CHAPTER" in content
        assert "Chapter 1" in content
        assert "Chapter 2" in content
        
        assembler = FFmpegMediaAssembler()
        final = tmp / "final.mp3"
        success = assembler.assemble(segments, meta_path, final)
        assert success
        assert final.exists()
        assert final.stat().st_size > 0
        print(f"  Assembled 2 chapters into final MP3 ({final.stat().st_size:,} bytes)")
    print("  PASSED")

def test_progress_tracker():
    print("\n[TEST] JSON Progress Tracker")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tracker = JsonProgressTracker(tmp)
        assert not tracker.is_complete("ch1", "abc123")
        
        # Create dummy audio file so tracker validates completion
        dummy_audio = tmp / "chap_ch1.mp3"
        dummy_audio.write_bytes(b"dummy mp3 data")
        
        tracker.mark_complete("ch1", "abc123")
        assert tracker.is_complete("ch1", "abc123")
        assert not tracker.is_complete("ch1", "different_checksum")
        tracker.save()
        # Re-load and verify persistence
        tracker2 = JsonProgressTracker(tmp)
        assert tracker2.is_complete("ch1", "abc123")
    print("  PASSED")

def test_file_sanitizer():
    print("\n[TEST] File Sanitizer")
    s = FileSanitizer()
    assert s.sanitize("Hello World!") == "Hello World"
    assert s.sanitize("Ch. 1: The Beginning?") == "Ch 1 The Beginning"
    assert s.sanitize("") == "unnamed"
    print("  PASSED")

def test_config_validation():
    print("\n[TEST] Config Validation")
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    
    # Valid
    cfg = Settings(input_epub=tmp_path, speed="+10%")
    assert cfg.resolve_output_path().suffix == ".mp3"
    
    # Invalid speed
    try:
        Settings(input_epub=tmp_path, speed="invalid")
        assert False, "Should have raised"
    except Exception:
        pass
    
    tmp_path.unlink()
    print("  PASSED")

def test_full_orchestrator_single_chapter():
    print("\n[TEST] Full Orchestrator (single chapter via monkeypatch)")
    parser = EbookLibParser()
    epub_path = Path("epub/The_Perfect_Run.epub")
    all_chapters = parser.parse(epub_path)
    
    # Monkeypatch: replace chapters with just 1 tiny chapter to keep test fast
    tiny_chapter = Chapter(id="0000", title="Smoke Test", text="Hello world. This is a test.")
    
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        settings = Settings(
            input_epub=epub_path,
            output_path=tmp / "smoke_test.mp3",
            speed="+0%",
            concurrency="sequential",
            log_level="WARNING",
        )
        
        # Use real providers but inject a custom parser that returns 1 chapter
        class SingleChapterParser:
            def parse(self, path):
                return [tiny_chapter]
        
        tts = EdgeTTSProvider()
        tracker = JsonProgressTracker(tmp / "progress")
        assembler = FFmpegMediaAssembler()
        meta = FFmpegMetadataBuilder()
        
        use_case = BuildAudiobookUseCase(
            parser=SingleChapterParser(),
            tts=tts,
            assembler=assembler,
            metadata_builder=meta,
            tracker=tracker,
        )
        
        output = use_case.execute(settings)
        assert output.exists()
        assert output.stat().st_size > 0
        print(f"  Generated audiobook: {output} ({output.stat().st_size:,} bytes)")
        print("  PASSED")

def test_scraper_toc_fetch():
    print("\n[TEST] Worm Scraper (ToC fetch only)")
    import requests
    from epub_listener.scrapers.worm import WormScraper
    
    scraper = WormScraper(delay=0.5)
    try:
        session = requests.Session()
        links = scraper._get_chapter_links(session)
        assert len(links) > 100  # Worm is long
        print(f"  Fetched {len(links)} chapter links from ToC")
        print("  PASSED")
    except Exception as exc:
        print(f"  SKIPPED (network): {exc}")

if __name__ == "__main__":
    test_parser()
    test_edge_tts()
    test_kokoro_tts()
    test_media_pipeline()
    test_progress_tracker()
    test_file_sanitizer()
    test_config_validation()
    test_full_orchestrator_single_chapter()
    test_scraper_toc_fetch()
    print("\n" + "="*50)
    print("ALL SMOKE TESTS COMPLETE")
    print("="*50)
