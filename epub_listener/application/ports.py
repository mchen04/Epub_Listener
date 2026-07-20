"""Application ports (abstract interfaces)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from epub_listener.domain.models import AudioSegment, Chapter


@dataclass(frozen=True)
class TTSJob:
    chapter_id: str
    text: str
    output: Path
    voice: str | None
    speed: str
    # None disables transcript capture for this job.
    transcript_path: Path | None = None


def transcript_path_for(audio_path: Path) -> Path:
    """Workspace transcript file for a chapter's audio file."""
    return audio_path.with_suffix(".transcript.json")


@dataclass(frozen=True)
class TTSResult:
    chapter_id: str
    duration_ms: int


GenerationCallback = Callable[[TTSResult], None]


class ChapterParser(Protocol):
    """Parses an EPUB file into a list of chapters."""

    def parse(self, epub_path: Path) -> list[Chapter]:
        """Extract chapters from the given EPUB file."""
        ...


class TTSBatchGenerator(Protocol):
    """Generates a batch of TTS jobs and reports durable completions serially."""

    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        """Generate many chapters and report each completed file.

        Implementations call ``on_complete`` serially after each durable output
        is written and its final duration is known. Any generation or callback
        failure aborts the batch and is propagated; callers must not assume a
        partial audiobook can be assembled after a failed batch. Each submitted
        chapter may be reported at most once, and results must reference a
        submitted chapter id.
        """
        ...


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


class TranscriptEmbedder(Protocol):
    """Combines per-chapter transcripts and embeds them into the final MP3."""

    def embed(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        engine: str,
        generation_key: str,
        output: Path,
    ) -> bool:
        """Embed the combined transcript; returns False (without raising) when
        chapter transcripts are missing or invalid so a build never fails over
        transcript trouble."""
        ...


class ProgressTracker(Protocol):
    """Tracks which chapters have already been processed."""

    def is_complete(
        self,
        chapter_id: str,
        checksum: str,
        generation_key: str | None = None,
    ) -> bool:
        """Check if a chapter with this checksum is already complete."""
        ...

    def cached_duration_ms(self, chapter_id: str) -> int:
        """Return the recorded audio duration for a completed chapter, or 0 if unknown."""
        ...

    def mark_complete(
        self,
        chapter_id: str,
        checksum: str,
        duration_ms: int,
        generation_key: str | None = None,
    ) -> None:
        """Mark a chapter complete and durably record its generated audio duration."""
        ...
