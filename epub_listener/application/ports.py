"""Application ports (abstract interfaces)."""

from pathlib import Path
from typing import Literal, Protocol

from epub_listener.domain.models import AudioSegment, Chapter

ConcurrencyStrategy = Literal["sequential", "async", "parallel"]


class ChapterParser(Protocol):
    """Parses an EPUB file into a list of chapters."""

    def parse(self, epub_path: Path) -> list[Chapter]:
        """Extract chapters from the given EPUB file."""
        ...


class TTSProvider(Protocol):
    """Generates audio from text."""

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate audio and return duration in milliseconds, or 0 on failure."""
        ...

    def supports_concurrency(self) -> ConcurrencyStrategy:
        """Return the concurrency strategy this provider supports."""
        ...

    async def generate_many(
        self,
        jobs: list[tuple[str, Path, str | None, str]],
    ) -> list[int]:
        """Generate audio for many chapters concurrently, returning per-job durations.

        Only providers that report ``supports_concurrency() == "async"`` need to
        implement this; the default raises for everyone else.
        """
        raise NotImplementedError


class MediaAssembler(Protocol):
    """Assembles audio segments and metadata into a final audiobook."""

    def assemble(
        self,
        segments: list[AudioSegment],
        metadata_path: Path,
        output: Path,
    ) -> None:
        """Merge segments into the final output. Raises AssemblyError on failure."""
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
    ) -> None:
        """Write the metadata file to ``output``."""
        ...


class ProgressTracker(Protocol):
    """Tracks which chapters have already been processed."""

    def is_complete(self, chapter_id: str, checksum: str) -> bool:
        """Check if a chapter with this checksum is already complete."""
        ...

    def cached_duration_ms(self, chapter_id: str) -> int:
        """Return the recorded audio duration for a completed chapter, or 0 if unknown."""
        ...

    def mark_complete(self, chapter_id: str, checksum: str, duration_ms: int) -> None:
        """Mark a chapter complete and durably record its generated audio duration."""
        ...
