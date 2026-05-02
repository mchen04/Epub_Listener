"""CLI argument parser."""

import argparse
from pathlib import Path

from epub_listener.config import Settings


def parse_args() -> Settings:
    """Parse CLI arguments into a Settings object."""
    parser = argparse.ArgumentParser(
        description="Convert an EPUB file into a narrated MP3 audiobook with chapters."
    )
    parser.add_argument("input_epub", type=Path, help="Path to the input EPUB file")
    parser.add_argument(
        "output_path",
        nargs="?",
        type=Path,
        default=None,
        help="Optional explicit output .mp3 path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="Directory to save audiobooks (default: outputs)",
    )
    parser.add_argument(
        "--speed",
        default="+0%",
        help="Playback speed modifier (e.g., +10%%, -20%%)",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Edge-TTS voice (e.g. en-US-AriaNeural)",
    )
    parser.add_argument(
        "--author",
        default="Michael Chen",
        help="Author metadata for the audiobook",
    )
    parser.add_argument(
        "--resume-dir",
        type=Path,
        default=None,
        help="Directory to resume a previous build from",
    )
    parser.add_argument(
        "--use-kokoro",
        action="store_true",
        help="Use local Kokoro-82M TTS instead of Edge-TTS",
    )
    parser.add_argument(
        "--kokoro-voice",
        default=None,
        help="Kokoro voice (e.g. af_heart, am_fenrir)",
    )
    parser.add_argument(
        "--kokoro-lang",
        default="a",
        help="Kokoro language code (default: a = American English)",
    )
    parser.add_argument(
        "--concurrency",
        choices=["sequential", "async", "parallel"],
        default="async",
        help="Concurrency strategy (default: async)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Max workers for parallel generation (default: 4)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )

    ns = parser.parse_args()
    return Settings(
        input_epub=ns.input_epub,
        output_path=ns.output_path,
        output_dir=ns.output_dir,
        speed=ns.speed,
        voice=ns.voice,
        author=ns.author,
        resume_dir=ns.resume_dir,
        use_kokoro=ns.use_kokoro,
        kokoro_voice=ns.kokoro_voice,
        kokoro_lang=ns.kokoro_lang,
        concurrency=ns.concurrency,
        max_workers=ns.max_workers,
        log_level=ns.log_level,
    )
