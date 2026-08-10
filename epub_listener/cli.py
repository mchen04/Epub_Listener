"""CLI argument parser."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from epub_listener.concurrency import CONCURRENCY_CHOICES
from epub_listener.config import Settings
from epub_listener.domain.exceptions import ConfigurationError
from epub_listener.domain.tts import COMMAND_OUTPUT_FORMATS, TTS_ENGINE_CHOICES


def parse_args() -> Settings:
    """Parse CLI arguments into a Settings object.

    Optional flags default to ``argparse.SUPPRESS`` so that anything the user
    does not pass is simply absent from the namespace and falls back to the
    Settings default — keeping every default defined in exactly one place.
    """
    parser = argparse.ArgumentParser(
        description="Convert an EPUB file into a narrated MP3 audiobook with chapters.",
        epilog=(
            "Examples: --engine huggingface --model facebook/mms-tts-eng; "
            "--engine command --model-command 'piper --model voice.onnx "
            "--output_file {output}'"
        ),
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
        help="Voice identifier or model voice preset",
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
        "--engine",
        choices=TTS_ENGINE_CHOICES,
        default=argparse.SUPPRESS,
        help="TTS engine (default: edge)",
    )
    parser.add_argument(
        "--model",
        default=argparse.SUPPRESS,
        help="Hugging Face model ID or local model directory",
    )
    parser.add_argument(
        "--revision",
        default=argparse.SUPPRESS,
        help="Hugging Face branch, tag, or commit (pin commits for reproducibility)",
    )
    parser.add_argument(
        "--device",
        default=argparse.SUPPRESS,
        help="Local inference device: auto, cpu, mps, cuda, cuda:0, ...",
    )
    parser.add_argument(
        "--dtype",
        choices=["auto", "float32", "float16", "bfloat16"],
        default=argparse.SUPPRESS,
        help="Hugging Face model precision (default: auto)",
    )
    parser.add_argument(
        "--model-options",
        type=_json_object,
        default=argparse.SUPPRESS,
        metavar="JSON|@FILE",
        help=(
            "Namespaced Hugging Face options with pipeline/preprocess/forward/generate "
            "objects; prefix a JSON file with @"
        ),
    )
    parser.add_argument(
        "--speaker-embedding",
        type=Path,
        default=argparse.SUPPRESS,
        help="SpeechT5-style speaker embedding (.npy or .json)",
    )
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Execute custom model repository code after reviewing it",
    )
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Use only locally cached Hugging Face files",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum characters per local-model call; 0 disables (default: 500)",
    )
    parser.add_argument(
        "--chunk-pause-ms",
        type=int,
        default=argparse.SUPPRESS,
        help="Silence between local-model chunks (default: 80 ms)",
    )
    parser.add_argument(
        "--model-command",
        default=argparse.SUPPRESS,
        help=(
            "Local TTS command template; must contain {output}. Text is sent on stdin "
            "and is also available through {text_file}"
        ),
    )
    parser.add_argument(
        "--command-output-format",
        choices=COMMAND_OUTPUT_FORMATS,
        default=argparse.SUPPRESS,
        help="Audio format written by --model-command (default: wav)",
    )
    parser.add_argument(
        "--model-timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="Timeout per local command chunk in seconds (default: 1800)",
    )
    parser.add_argument(
        "--use-kokoro",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Legacy alias for --engine kokoro",
    )
    parser.add_argument(
        "--kokoro-voice",
        default=argparse.SUPPRESS,
        help="Legacy alias for --voice with a Kokoro engine",
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


def _json_object(value: str) -> dict[str, Any]:
    """Argparse converter for inline JSON or an ``@path`` JSON document."""
    source = value
    if value.startswith("@"):
        path = Path(value[1:])
        try:
            if path.stat().st_size > 1024 * 1024:
                raise argparse.ArgumentTypeError("model options file exceeds 1 MiB")
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise argparse.ArgumentTypeError(f"could not read model options {path}: {exc}") from exc
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON model options: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("model options must be a JSON object")
    return parsed
