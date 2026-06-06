"""Domain models."""

import hashlib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path


@dataclass(frozen=True)
class Chapter:
    """A single chapter extracted from an EPUB."""

    id: str
    title: str
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @cached_property
    def checksum(self) -> str:
        """SHA256 hexdigest of the chapter text (computed once per instance)."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AudioSegment:
    """A generated audio file for a chapter."""

    path: Path
    duration_ms: int
    chapter_id: str


@dataclass(frozen=True)
class AudiobookProject:
    """Aggregates the data for a single audiobook build."""

    title: str
    author: str
    chapters: list[Chapter]
    temp_dir: Path
