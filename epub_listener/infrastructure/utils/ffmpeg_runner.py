"""Safe ffmpeg subprocess runner."""

import logging
import subprocess
from pathlib import Path

from epub_listener.domain.exceptions import AssemblyError

logger = logging.getLogger(__name__)


def run_ffmpeg(*args: str | Path, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Run an ffmpeg command with unified error handling.

    Args:
        *args: Command arguments.
        timeout: Maximum seconds to wait before killing the process.

    Returns:
        CompletedProcess on success.

    Raises:
        AssemblyError: If the command fails or times out.
    """
    cmd = ["ffmpeg", "-y", *[str(a) for a in args]]
    logger.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("ffmpeg timed out after %ds", timeout)
        raise AssemblyError(f"ffmpeg timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed: %s", exc.stderr)
        raise AssemblyError(f"ffmpeg failed: {exc.stderr}") from exc
    except FileNotFoundError as exc:
        logger.error("ffmpeg not found in PATH")
        raise AssemblyError("ffmpeg not found. Is FFmpeg installed?") from exc
    return result
