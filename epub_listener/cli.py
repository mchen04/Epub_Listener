"""CLI argument parser."""

import argparse
from pathlib import Path

from epub_listener.config import Settings


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
        "--concurrency",
        choices=["sequential", "async", "parallel"],
        default=argparse.SUPPRESS,
        help="Concurrency strategy (default: async)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=argparse.SUPPRESS,
        help="Max workers for parallel generation (default: 4)",
    )
    parser.add_argument(
        "--log-level",
        default=argparse.SUPPRESS,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )

    ns = parser.parse_args()
    return Settings(**vars(ns))
