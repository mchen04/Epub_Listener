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
        self._state: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        if self._progress_file.exists():
            try:
                with open(self._progress_file, encoding="utf-8") as f:
                    self._state = json.load(f)
                logger.info("Loaded progress tracker from %s", self._progress_file)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not load progress file, starting fresh: %s", exc)
                self._state = {}
        else:
            self._state = {}

    def save(self) -> None:
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
        audio_path = self._temp_dir / f"chap_{chapter_id}.mp3"
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            logger.info("Chapter %s audio missing or empty, regenerating.", chapter_id)
            return False
        return True

    def mark_complete(self, chapter_id: str, checksum: str) -> None:
        self._state[chapter_id] = {"checksum": checksum}
        self.save()

    def get_existing_segments(self) -> dict[str, Path]:
        segments: dict[str, Path] = {}
        for chapter_id in self._state:
            path = self._temp_dir / f"chap_{chapter_id}.mp3"
            if path.exists() and path.stat().st_size > 0:
                segments[chapter_id] = path
        return segments
