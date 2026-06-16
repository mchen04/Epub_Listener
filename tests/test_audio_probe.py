import subprocess
from pathlib import Path
from typing import Any

import pytest

from epub_listener.domain.exceptions import AudioProbeError
from epub_listener.infrastructure.utils import audio_probe


def test_get_audio_duration_raises_typed_error_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: Any, timeout: int, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=timeout)

    monkeypatch.setattr(audio_probe.subprocess, "run", fake_run)

    with pytest.raises(AudioProbeError, match="timed out after 7s"):
        audio_probe.get_audio_duration_ms(tmp_path / "stuck.mp3", timeout=7)


def test_get_audio_duration_parses_successful_ffprobe_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: Any, timeout: int, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert timeout == 9
        return subprocess.CompletedProcess(
            args="ffprobe", returncode=0, stdout='{"format":{"duration":"1.234"}}'
        )

    monkeypatch.setattr(audio_probe.subprocess, "run", fake_run)

    assert audio_probe.get_audio_duration_ms(tmp_path / "ok.mp3", timeout=9) == 1234


def test_get_audio_duration_wraps_nonzero_ffprobe_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: Any, timeout: int, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd="ffprobe", stderr="bad input")

    monkeypatch.setattr(audio_probe.subprocess, "run", fake_run)

    with pytest.raises(AudioProbeError, match="ffprobe failed"):
        audio_probe.get_audio_duration_ms(tmp_path / "bad.mp3")


def test_get_audio_duration_wraps_missing_ffprobe_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: Any, timeout: int, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(audio_probe.subprocess, "run", fake_run)

    with pytest.raises(AudioProbeError, match="ffprobe not found"):
        audio_probe.get_audio_duration_ms(tmp_path / "missing.mp3")


@pytest.mark.parametrize(
    "stdout",
    [
        "{not-json",
        "{}",
        '{"format":{}}',
        '{"format":{"duration":"not-a-number"}}',
    ],
)
def test_get_audio_duration_wraps_malformed_ffprobe_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    def fake_run(*args: Any, timeout: int, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args="ffprobe", returncode=0, stdout=stdout)

    monkeypatch.setattr(audio_probe.subprocess, "run", fake_run)

    with pytest.raises(AudioProbeError, match="Failed to parse ffprobe output"):
        audio_probe.get_audio_duration_ms(tmp_path / "bad.mp3")
