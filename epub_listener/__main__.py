"""Composition root and entry point."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from epub_listener.application.orchestrator import BuildAudiobookUseCase
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

    parser = EbookLibParser()
    tts: EdgeTTSProvider | KokoroTTSProvider = (
        KokoroTTSProvider() if settings.use_kokoro else EdgeTTSProvider()
    )

    temp_dir = settings.resume_dir if settings.resume_dir else None
    tracker = JsonProgressTracker(temp_dir or Path(".epub_listener_progress"))
    assembler = FFmpegMediaAssembler()
    metadata_builder = FFmpegMetadataBuilder()

    use_case = BuildAudiobookUseCase(
        parser=parser,
        tts=tts,
        assembler=assembler,
        metadata_builder=metadata_builder,
        tracker=tracker,
    )

    try:
        output = use_case.execute(settings)
        print(f"\nSuccess! Audiobook saved to {output}")
        return 0
    except EpubListenerError as exc:
        logging.error("Build failed: %s", exc)
        print(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        logging.warning("Build interrupted by user.")
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
