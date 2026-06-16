"""JSON-based progress tracker with checksum validation."""

import json
import logging
from pathlib import Path
from typing import TypeGuard

from epub_listener.application.ports import ProgressTracker
from epub_listener.domain.exceptions import ResumeError
from epub_listener.infrastructure.utils.durable_file import write_text_durably

logger = logging.getLogger(__name__)

_PROGRESS_FILE = "progress.json"


class JsonProgressTracker(ProgressTracker):
    """Tracks progress via JSON chapter checksums and generated durations."""

    def __init__(self, temp_dir: Path) -> None:
        self._temp_dir = temp_dir
        try:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ResumeError(
                f"Could not prepare progress directory {self._temp_dir}: {exc}"
            ) from exc
        self._progress_file = self._temp_dir / _PROGRESS_FILE
        self._state: dict[str, dict[str, str | int]] = {}
        self._load()

    def _load(self) -> None:
        if self._progress_file.exists():
            try:
                with open(self._progress_file, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Progress file root must be a JSON object")
                self._state = self._validate_state(data)
                logger.info("Loaded progress tracker from %s", self._progress_file)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                raise ResumeError(
                    f"Could not load progress file {self._progress_file}: {exc}"
                ) from exc
        else:
            self._state = {}

    def _validate_state(self, data: object) -> dict[str, dict[str, str | int]]:
        if not isinstance(data, dict):
            raise ValueError("Progress file root must be a JSON object")
        state: dict[str, dict[str, str | int]] = {}
        for chapter_id, entry in data.items():
            if not isinstance(chapter_id, str):
                raise ValueError("Progress chapter ids must be strings")
            if not isinstance(entry, dict):
                raise ValueError(f"Progress entry for {chapter_id} must be an object")
            checksum = entry.get("checksum")
            duration_ms = entry.get("duration_ms")
            generation_key = entry.get("generation_key", "")
            if not isinstance(checksum, str):
                raise ValueError(f"Progress entry for {chapter_id} has invalid checksum")
            if not self._is_valid_duration(duration_ms):
                raise ValueError(f"Progress entry for {chapter_id} has invalid duration_ms")
            if not isinstance(generation_key, str):
                raise ValueError(f"Progress entry for {chapter_id} has invalid generation_key")
            entry_state: dict[str, str | int] = {
                "checksum": checksum,
                "duration_ms": duration_ms,
            }
            if generation_key:
                entry_state["generation_key"] = generation_key
            state[chapter_id] = entry_state
        return state

    def _is_valid_duration(self, duration_ms: object) -> TypeGuard[int]:
        return type(duration_ms) is int and duration_ms > 0

    def _save(self, state: dict[str, dict[str, str | int]]) -> None:
        try:
            payload = json.dumps(state, indent=2) + "\n"
            write_text_durably(self._progress_file, payload, prefix=".progress.")
        except OSError as exc:
            raise ResumeError(f"Failed to save progress: {exc}") from exc

    def is_complete(
        self,
        chapter_id: str,
        checksum: str,
        generation_key: str | None = None,
    ) -> bool:
        entry = self._state.get(chapter_id)
        if not entry:
            return False
        stored_checksum = entry.get("checksum", "")
        if stored_checksum != checksum:
            logger.info("Chapter %s checksum changed, regenerating.", chapter_id)
            return False
        if generation_key is not None and entry.get("generation_key", "") != generation_key:
            logger.info("Chapter %s generation settings changed, regenerating.", chapter_id)
            return False
        return True

    def cached_duration_ms(self, chapter_id: str) -> int:
        entry = self._state.get(chapter_id)
        if not entry:
            return 0
        try:
            return int(entry.get("duration_ms", 0))
        except (TypeError, ValueError):
            return 0

    def mark_complete(
        self,
        chapter_id: str,
        checksum: str,
        duration_ms: int,
        generation_key: str | None = None,
    ) -> None:
        if not self._is_valid_duration(duration_ms):
            raise ResumeError(f"Invalid duration for chapter {chapter_id}: {duration_ms}")
        state = dict(self._state)
        entry: dict[str, str | int] = {"checksum": checksum, "duration_ms": duration_ms}
        if generation_key is not None:
            entry["generation_key"] = generation_key
        state[chapter_id] = entry
        self._save(state)
        self._state = state
