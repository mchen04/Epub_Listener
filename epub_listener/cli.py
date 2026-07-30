"""CLI argument parser."""

import argparse
from pathlib import Path

from pydantic import ValidationError

from epub_listener.concurrency import CONCURRENCY_CHOICES
from epub_listener.config import Settings
from epub_listener.domain.exceptions import ConfigurationError


def parse_args() -> Settings:
    """Parse CLI arguments into a Settings object.

    Optional flags default to ``argparse.SUPPRESS`` so that anything the user
    does not pass is simply absent from the namespace and falls back to the
    Settings default — keeping every default defined in exactly one place.
    """
    parser = argparse.ArgumentParser(
        description="Convert an EPUB file into a narrated MP3 audiobook with chapters."
    )
    parser.add_argument("input_epub", type=Path, help="Path to the input EPUB file")
    parser.add_argument(
        "output_path",
        nargs="?",
        type=Path,
        default=argparse.SUPPRESS,
        help="Optional explicit output .mp3 path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Directory to save audiobooks (default: outputs)",
    )
    parser.add_argument(
        "--speed",
        default=argparse.SUPPRESS,
        help="Playback speed modifier (e.g., +10%%, -20%%)",
    )
    parser.add_argument(
        "--voice",
        default=argparse.SUPPRESS,
        help="Edge-TTS voice (e.g. en-US-AriaNeural)",
    )
    parser.add_argument(
        "--author",
        default=argparse.SUPPRESS,
        help="Author metadata for the audiobook",
    )
    parser.add_argument(
        "--title",
        default=argparse.SUPPRESS,
        help="Title metadata for the audiobook (default: EPUB filename)",
    )
    parser.add_argument(
        "--resume-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="Directory to resume a previous build from",
    )
    parser.add_argument(
        "--use-kokoro",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Use local Kokoro-82M TTS instead of Edge-TTS",
    )
    parser.add_argument(
        "--kokoro-voice",
        default=argparse.SUPPRESS,
        help="Kokoro voice (e.g. af_heart, am_fenrir)",
    )
    parser.add_argument(
        "--kokoro-hybrid-mps",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Use a tuned Apple MPS + CPU Kokoro worker pair",
    )
    parser.add_argument(
        "--kokoro-mlx",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Use Apple MLX for faster Kokoro inference",
    )
    parser.add_argument(
        "--kokoro-preset",
        default=argparse.SUPPRESS,
        help=(
            "FastKokoro model preset: ship-q8 (default, balanced), ship-q4 (smaller), "
            "exact (highest fidelity), student-fast (fastest; af_heart only, "
            "+0%% speed only, no word timings)"
        ),
    )
    parser.add_argument(
        "--no-transcript",
        action="store_false",
        dest="transcript",
        default=argparse.SUPPRESS,
        help="Do not capture word timings or embed a read-along transcript",
    )
    parser.add_argument(
        "--concurrency",
        choices=CONCURRENCY_CHOICES,
        default=argparse.SUPPRESS,
        help="Concurrency strategy (default: auto)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=argparse.SUPPRESS,
        help="Max concurrent TTS jobs (default: 4)",
    )
    parser.add_argument(
        "--log-level",
        default=argparse.SUPPRESS,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )

    ns = parser.parse_args()
    try:
        return Settings(**vars(ns))
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid configuration: {_format_validation_error(exc)}") from exc


def _format_validation_error(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        message = str(error["msg"]).removeprefix("Value error, ")
        # Whole-model validators carry no location; don't emit a bare ": ".
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages)
