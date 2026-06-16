"""Finalize generated TTS output files atomically."""

from collections.abc import Callable
from pathlib import Path

from epub_listener.domain.exceptions import AudioProbeError, TTSGenerationError
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms
from epub_listener.infrastructure.utils.durable_file import durably_replace


def commit_generated_mp3(
    tmp_output: Path,
    output: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    """Validate a temporary MP3, atomically replace the final file, and return duration."""
    if should_cancel and should_cancel():
        raise TTSGenerationError("TTS batch cancelled")
    if not tmp_output.exists() or tmp_output.stat().st_size == 0:
        raise TTSGenerationError(f"TTS produced no MP3 for {output}")

    try:
        duration_ms = get_audio_duration_ms(tmp_output)
    except AudioProbeError as exc:
        raise TTSGenerationError(f"Could not validate generated MP3 for {output}: {exc}") from exc
    if duration_ms <= 0:
        raise TTSGenerationError(f"TTS produced invalid duration for {output}")
    if should_cancel and should_cancel():
        raise TTSGenerationError("TTS batch cancelled")

    try:
        durably_replace(tmp_output, output)
    except OSError as exc:
        raise TTSGenerationError(f"Failed to commit generated MP3 for {output}: {exc}") from exc
    return duration_ms
