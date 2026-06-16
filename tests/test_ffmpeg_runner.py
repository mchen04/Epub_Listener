import subprocess
from pathlib import Path

import pytest

from epub_listener.domain.exceptions import AssemblyError
from epub_listener.infrastructure.utils import ffmpeg_runner


def test_run_ffmpeg_wraps_subprocess_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, timeout: int, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout, stderr="partial")

    monkeypatch.setattr(ffmpeg_runner.subprocess, "run", fake_run)

    with pytest.raises(AssemblyError, match="timed out after 7s"):
        ffmpeg_runner.run_ffmpeg(Path("in.mp3"), timeout=7)


class HangingProcess:
    def __init__(self) -> None:
        self.killed = False
        self.communicate_calls = 0
        self.returncode: int | None = None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        if self.killed:
            self.returncode = -9
            return "", "partial"
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout or 0)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def poll(self) -> int | None:
        return self.returncode


def test_run_ffmpeg_cancellation_kills_and_drains_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = HangingProcess()
    monkeypatch.setattr(ffmpeg_runner.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(AssemblyError, match="cancelled"):
        ffmpeg_runner.run_ffmpeg("in.wav", timeout=30, should_cancel=lambda: True)

    assert process.killed
    assert process.communicate_calls == 2


def test_run_ffmpeg_cancellable_timeout_kills_and_drains_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = HangingProcess()
    monkeypatch.setattr(ffmpeg_runner.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(AssemblyError, match="timed out after 0s"):
        ffmpeg_runner.run_ffmpeg("in.wav", timeout=0, should_cancel=lambda: False)

    assert process.killed
    assert process.communicate_calls == 2
