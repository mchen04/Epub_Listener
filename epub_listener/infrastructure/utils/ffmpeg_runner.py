"""Safe ffmpeg subprocess runner."""

import logging
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from epub_listener.domain.exceptions import AssemblyError

logger = logging.getLogger(__name__)


def run_ffmpeg(
    *args: str | Path,
    timeout: int = 300,
    should_cancel: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an ffmpeg command with unified error handling.

    Args:
        *args: Command arguments.
        timeout: Maximum seconds to wait before killing the process.
        should_cancel: Optional cancellation predicate checked while ffmpeg runs.

    Returns:
        CompletedProcess on success.

    Raises:
        AssemblyError: If the command fails or times out.
    """
    cmd = ["ffmpeg", "-y", *[str(a) for a in args]]
    logger.debug("Running: %s", " ".join(cmd))
    if should_cancel is not None:
        return _run_cancellable_ffmpeg(cmd, timeout, should_cancel)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("ffmpeg timed out after %ds. Partial stderr: %s", timeout, exc.stderr or "")
        raise AssemblyError(f"ffmpeg timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed: %s", exc.stderr)
        raise AssemblyError(f"ffmpeg failed: {exc.stderr}") from exc
    except FileNotFoundError as exc:
        logger.error("ffmpeg not found in PATH")
        raise AssemblyError("ffmpeg not found. Is FFmpeg installed?") from exc
    return result


def _run_cancellable_ffmpeg(
    cmd: list[str],
    timeout: int,
    should_cancel: Callable[[], bool],
) -> subprocess.CompletedProcess[str]:
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        logger.error("ffmpeg not found in PATH")
        raise AssemblyError("ffmpeg not found. Is FFmpeg installed?") from exc

    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                if should_cancel():
                    logger.error("ffmpeg cancelled. Partial stderr: %s", stderr or "")
                    raise AssemblyError("ffmpeg cancelled")
                break
            except subprocess.TimeoutExpired:
                if should_cancel():
                    process.kill()
                    stdout, stderr = process.communicate()
                    logger.error("ffmpeg cancelled. Partial stderr: %s", stderr or "")
                    raise AssemblyError("ffmpeg cancelled") from None
                if time.monotonic() >= deadline:
                    process.kill()
                    stdout, stderr = process.communicate()
                    logger.error(
                        "ffmpeg timed out after %ds. Partial stderr: %s",
                        timeout,
                        stderr or "",
                    )
                    raise AssemblyError(f"ffmpeg timed out after {timeout}s") from None
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.communicate()
        raise

    if process.returncode != 0:
        logger.error("ffmpeg failed: %s", stderr)
        raise AssemblyError(f"ffmpeg failed: {stderr}")
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
