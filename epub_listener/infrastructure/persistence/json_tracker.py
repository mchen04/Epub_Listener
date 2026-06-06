"""JSON-based progress tracker with checksum validation."""

import json
import logging
from pathlib import Path

from epub_listener.application.ports import ProgressTracker

logger = logging.getLogger(__name__)

_PROGRESS_FILE = "progress.json"


class JsonProgressTracker(ProgressTracker):
    """Tracks progress via a JSON file containing chapter checksums."""

    def __init__(self, temp_dir: Path) -> None:
        self._temp_dir = temp_dir
        self._temp_dir.mkdir(parents=True, exist_ok=True)
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
                self._state = data
                logger.info("Loaded progress tracker from %s", self._progress_file)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                logger.warning("Could not load progress file, starting fresh: %s", exc)
                self._state = {}
        else:
            self._state = {}

    def _save(self) -> None:
        try:
            with open(self._progress_file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
        except OSError as exc:
            logger.error("Failed to save progress: %s", exc)

    def is_complete(self, chapter_id: str, checksum: str) -> bool:
        entry = self._state.get(chapter_id)
        if not entry:
            return False
        stored_checksum = entry.get("checksum", "")
        if stored_checksum != checksum:
            logger.info("Chapter %s checksum changed, regenerating.", chapter_id)
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

    def mark_complete(self, chapter_id: str, checksum: str, duration_ms: int) -> None:
        self._state[chapter_id] = {"checksum": checksum, "duration_ms": duration_ms}
        self._save()
