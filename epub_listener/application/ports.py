"""Application ports (abstract interfaces)."""

from pathlib import Path
from typing import Protocol

from epub_listener.domain.models import AudioSegment, Chapter


class ChapterParser(Protocol):
    """Parses an EPUB file into a list of chapters."""

    def parse(self, epub_path: Path) -> list[Chapter]:
        """Extract chapters from the given EPUB file."""
        ...


class TTSProvider(Protocol):
    """Generates audio from text."""

    def generate(
        self,
        text: str,
        output: Path,
        voice: str | None,
        speed: str,
    ) -> int:
        """Generate audio and return duration in milliseconds, or 0 on failure."""
        ...

    def supports_concurrency(self) -> str:
        """Return concurrency strategy: 'sequential', 'async', or 'parallel'."""
        ...

    async def generate_many(
        self,
        jobs: list[tuple[str, Path, str | None, str]],
    ) -> list[int]:
        """Concurrent generation for multiple chapters.

        Default implementation raises NotImplementedError.
        Providers that support async concurrency should override.
        """
        raise NotImplementedError


class MediaAssembler(Protocol):
    """Assembles audio segments and metadata into a final audiobook."""

    def assemble(
        self,
        segments: list[AudioSegment],
        metadata_path: Path,
        output: Path,
    ) -> bool:
        """Merge segments into final output. Returns True on success."""
        ...


class MetadataBuilder(Protocol):
    """Builds chapter metadata files (e.g., FFMETADATA1)."""

    def build(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        book_title: str,
        book_author: str,
        output: Path,
    ) -> Path:
        """Write metadata file and return its path."""
        ...


class ProgressTracker(Protocol):
    """Tracks which chapters have already been processed."""

    def is_complete(self, chapter_id: str, checksum: str) -> bool:
        """Check if a chapter with this checksum is already complete."""
        ...

    def mark_complete(self, chapter_id: str, checksum: str) -> None:
        """Mark a chapter as complete."""
        ...

    def get_existing_segments(self) -> dict[str, Path]:
        """Return mapping of chapter_id -> existing audio file path."""
        ...

    def save(self) -> None:
        """Persist tracker state."""
        ...


class FileSanitizer(Protocol):
    """Sanitizes strings for safe filesystem usage."""

    def sanitize(self, name: str) -> str: ...
