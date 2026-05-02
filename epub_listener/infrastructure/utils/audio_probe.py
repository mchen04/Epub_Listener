"""Audio file probing utilities."""

import json
import logging
import subprocess
from pathlib import Path

from epub_listener.domain.exceptions import AudioProbeError

logger = logging.getLogger(__name__)


def get_audio_duration_ms(audio_file_path: Path) -> int:
    """Return audio duration in milliseconds using ffprobe.

    Args:
        audio_file_path: Path to the audio file.

    Returns:
        Duration in milliseconds, or 0 on failure.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(audio_file_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        logger.error("ffprobe not found in PATH")
        raise AudioProbeError("ffprobe not found. Is FFmpeg installed?") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("ffprobe failed for %s: %s", audio_file_path, exc.stderr)
        raise AudioProbeError(f"ffprobe failed for {audio_file_path}") from exc

    try:
        data = json.loads(result.stdout)
        duration_sec = float(data["format"]["duration"])
        return int(duration_sec * 1000)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.error("Failed to parse ffprobe output for %s", audio_file_path)
        raise AudioProbeError(f"Failed to parse ffprobe output for {audio_file_path}") from exc
