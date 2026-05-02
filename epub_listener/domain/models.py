"""Domain models."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Chapter:
    """A single chapter extracted from an EPUB."""

    id: str
    title: str
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def checksum(self) -> str:
        """SHA256 hexdigest of the chapter text."""
        import hashlib

        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AudioSegment:
    """A generated audio file for a chapter."""

    path: Path
    duration_ms: int
    chapter_id: str


@dataclass
class AudiobookProject:
    """Aggregates all data for a single audiobook build."""

    title: str
    author: str
    chapters: list[Chapter]
    output_path: Path
    temp_dir: Path


@dataclass
class BuildConfig:
    """User-facing build configuration."""

    input_epub: Path
    output_path: Path | None
    output_dir: Path
    speed: str
    voice: str | None
    author: str
    resume_dir: Path | None
    use_kokoro: bool
    kokoro_voice: str | None
    kokoro_lang: str
    concurrency: Literal["sequential", "async", "parallel"] = "async"
    max_workers: int = 4
