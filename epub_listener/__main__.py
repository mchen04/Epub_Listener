"""Composition root and entry point."""

import logging
import shutil
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from epub_listener.application.orchestrator import BuildAudiobookUseCase
from epub_listener.application.ports import TTSProvider
from epub_listener.cli import parse_args
from epub_listener.domain.exceptions import EpubListenerError
from epub_listener.infrastructure.media.ffmpeg_assembler import FFmpegMediaAssembler
from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder
from epub_listener.infrastructure.parsers.ebooklib_parser import EbookLibParser
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker
from epub_listener.infrastructure.tts.edge_tts import EdgeTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import KokoroTTSProvider


def setup_logging(log_level: str, log_dir: Path = Path("logs")) -> None:
    """Configure unified logging to console and rotating file."""
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler
    file_handler = RotatingFileHandler(
        log_dir / "epub_listener.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def main() -> int:
    """CLI entry point."""
    settings = parse_args()
    setup_logging(settings.log_level)

    if settings.resume_dir and settings.resume_dir.exists():
        temp_dir = settings.resume_dir
        auto_created = False
        logging.info("Resuming from: %s", temp_dir)
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="epub_audiobook_"))
        auto_created = True

    try:
        tts: TTSProvider = KokoroTTSProvider() if settings.use_kokoro else EdgeTTSProvider()
        use_case = BuildAudiobookUseCase(
            parser=EbookLibParser(),
            tts=tts,
            assembler=FFmpegMediaAssembler(),
            metadata_builder=FFmpegMetadataBuilder(),
            tracker=JsonProgressTracker(temp_dir),
        )
        output = use_case.execute(settings, temp_dir=temp_dir)
        print(f"\nSuccess! Audiobook saved to {output}")
        if auto_created:
            try:
                shutil.rmtree(temp_dir)
            except OSError as exc:
                logging.warning("Could not remove temp dir %s: %s", temp_dir, exc)
        return 0
    except EpubListenerError as exc:
        logging.error("Build failed: %s", exc)
        print(f"Error: {exc}")
        if auto_created:
            print(f"Retry with: --resume-dir {temp_dir}")
        return 1
    except KeyboardInterrupt:
        logging.warning("Build interrupted by user.")
        print("\nInterrupted.")
        if auto_created:
            print(f"Resume with: --resume-dir {temp_dir}")
        return 130


if __name__ == "__main__":
    sys.exit(main())
