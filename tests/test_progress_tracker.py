from pathlib import Path

import pytest

from epub_listener.domain.exceptions import ResumeError
from epub_listener.infrastructure.persistence import json_tracker as json_tracker_module
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker


def test_progress_tracker_does_not_mutate_memory_when_atomic_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = JsonProgressTracker(tmp_path)

    def fail_write(output_path: Path, content: str, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(json_tracker_module, "write_text_durably", fail_write)

    with pytest.raises(ResumeError):
        tracker.mark_complete("ch1", "abc123", 1000)

    assert not tracker.is_complete("ch1", "abc123")
    assert not (tmp_path / "progress.json").exists()


def test_progress_tracker_wraps_invalid_progress_directory(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress"
    progress_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ResumeError, match="Could not prepare progress directory"):
        JsonProgressTracker(progress_path)


def test_progress_tracker_rejects_invalid_progress_file(tmp_path: Path) -> None:
    (tmp_path / "progress.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ResumeError, match="Could not load progress file"):
        JsonProgressTracker(tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        '{"ch1": "bad"}',
        '{"ch1": {"checksum": 123, "duration_ms": 1000}}',
        '{"ch1": {"checksum": "abc123", "duration_ms": "1000"}}',
        '{"ch1": {"checksum": "abc123", "duration_ms": true}}',
        '{"ch1": {"checksum": "abc123", "duration_ms": 0}}',
        '{"ch1": {"checksum": "abc123", "duration_ms": -1}}',
        '{"ch1": {"checksum": "abc123", "duration_ms": 1000, "generation_key": 123}}',
    ],
)
def test_progress_tracker_rejects_malformed_progress_entries(
    tmp_path: Path,
    payload: str,
) -> None:
    (tmp_path / "progress.json").write_text(payload, encoding="utf-8")

    with pytest.raises(ResumeError, match="Could not load progress file"):
        JsonProgressTracker(tmp_path)


@pytest.mark.parametrize("duration_ms", [True, 0, -1])
def test_progress_tracker_rejects_invalid_mark_complete_duration(
    tmp_path: Path,
    duration_ms: int,
) -> None:
    tracker = JsonProgressTracker(tmp_path)

    with pytest.raises(ResumeError, match="Invalid duration"):
        tracker.mark_complete("ch1", "abc123", duration_ms)


def test_progress_tracker_matches_generation_key(tmp_path: Path) -> None:
    tracker = JsonProgressTracker(tmp_path)
    tracker.mark_complete("ch1", "abc123", 1000, "voice=a\nspeed=+0%")

    assert tracker.is_complete("ch1", "abc123")
    assert tracker.is_complete("ch1", "abc123", "voice=a\nspeed=+0%")
    assert not tracker.is_complete("ch1", "abc123", "voice=b\nspeed=+0%")

    reloaded = JsonProgressTracker(tmp_path)
    assert reloaded.is_complete("ch1", "abc123", "voice=a\nspeed=+0%")
