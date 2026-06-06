"""Build audiobook use case — pure orchestration over ports."""

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from epub_listener.application.ports import (
    ChapterParser,
    MediaAssembler,
    MetadataBuilder,
    ProgressTracker,
    TTSProvider,
)
from epub_listener.config import Settings
from epub_listener.domain.exceptions import EpubListenerError
from epub_listener.domain.models import AudiobookProject, AudioSegment, Chapter

logger = logging.getLogger(__name__)


class BuildAudiobookUseCase:
    """Orchestrates the full EPUB -> audiobook pipeline."""

    def __init__(
        self,
        parser: ChapterParser,
        tts: TTSProvider,
        assembler: MediaAssembler,
        metadata_builder: MetadataBuilder,
        tracker: ProgressTracker,
    ) -> None:
        self._parser = parser
        self._tts = tts
        self._assembler = assembler
        self._metadata_builder = metadata_builder
        self._tracker = tracker

    def execute(self, settings: Settings, *, temp_dir: Path) -> Path:
        """Run the full build pipeline.

        Args:
            settings: Build configuration.
            temp_dir: Directory for intermediate audio files and the progress
                tracker state.  The caller owns this directory's lifecycle —
                creation and cleanup happen outside this method.

        Returns:
            Path to the final audiobook MP3.

        Raises:
            EpubListenerError: On any unrecoverable failure.
        """
        output_path = settings.resolve_output_path()
        if output_path.suffix.lower() != ".mp3":
            raise EpubListenerError(f"Output file must end with .mp3 (got '{output_path.suffix}')")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Starting conversion for: %s", settings.input_epub)

        logger.info("Step 1/4: Parsing EPUB...")
        chapters = self._parser.parse(settings.input_epub)
        if not chapters:
            raise EpubListenerError("No chapters found in the EPUB file.")
        logger.info("Found %d chapters.", len(chapters))

        project = AudiobookProject(
            title=settings.input_epub.stem,
            author=settings.author,
            chapters=chapters,
            temp_dir=temp_dir,
        )

        logger.info("Step 2/4: Generating chapter audio...")
        segments = self._generate_audio(project, settings)
        if not segments:
            raise EpubListenerError("Failed to process any valid chapters.")

        logger.info("Step 3/4: Compiling chapter metadata...")
        meta_path = project.temp_dir / "ffmetadata.txt"
        chapter_titles = {ch.id: ch.title for ch in project.chapters}
        self._metadata_builder.build(
            segments, chapter_titles, project.title, project.author, meta_path
        )

        logger.info("Step 4/4: Exporting final audiobook: %s", output_path)
        self._assembler.assemble(segments, meta_path, output_path)

        logger.info("Success! Audiobook saved to %s", output_path)
        return output_path

    def _generate_audio(self, project: AudiobookProject, settings: Settings) -> list[AudioSegment]:
        strategy = settings.concurrency
        provider_strategy = self._tts.supports_concurrency()

        if strategy in ("async", "parallel") and strategy != provider_strategy:
            logger.warning(
                "Provider does not support '%s' concurrency; falling back to sequential.",
                strategy,
            )
            strategy = "sequential"

        if strategy == "async":
            return self._generate_async(project, settings)
        if strategy == "parallel":
            return self._generate_parallel(project, settings)
        return self._generate_sequential(project, settings)

    def _generate_sequential(
        self, project: AudiobookProject, settings: Settings
    ) -> list[AudioSegment]:
        segments, pending = self._partition_pending(project)
        for chapter in pending:
            logger.info("  [a] Generating: %s", chapter.title)
            audio_path = self._audio_path(chapter, project)
            duration = self._tts.generate(
                chapter.text, audio_path, settings.resolved_voice, settings.speed
            )
            seg = self._record_generated(chapter, audio_path, duration)
            if seg:
                segments.append(seg)
        return segments

    def _generate_async(self, project: AudiobookProject, settings: Settings) -> list[AudioSegment]:
        segments, pending = self._partition_pending(project)
        if pending:

            async def _run() -> list[int]:
                jobs = [
                    (ch.text, self._audio_path(ch, project), settings.resolved_voice, settings.speed)
                    for ch in pending
                ]
                return await self._tts.generate_many(jobs)

            durations = asyncio.run(_run())
            for chapter, duration in zip(pending, durations, strict=True):
                seg = self._record_generated(chapter, self._audio_path(chapter, project), duration)
                if seg:
                    segments.append(seg)
        return segments

    def _generate_parallel(
        self, project: AudiobookProject, settings: Settings
    ) -> list[AudioSegment]:
        segments, pending = self._partition_pending(project)
        if pending:
            with ProcessPoolExecutor(max_workers=settings.max_workers) as executor:
                futures = {
                    executor.submit(
                        self._tts.generate,
                        chapter.text,
                        self._audio_path(chapter, project),
                        settings.resolved_voice,
                        settings.speed,
                    ): chapter
                    for chapter in pending
                }
                for future in futures:
                    chapter = futures[future]
                    try:
                        duration = future.result()
                    except Exception:
                        logger.exception("Exception generating chapter %s", chapter.id)
                        continue
                    seg = self._record_generated(
                        chapter, self._audio_path(chapter, project), duration
                    )
                    if seg:
                        segments.append(seg)
        return segments

    def _partition_pending(
        self, project: AudiobookProject
    ) -> tuple[list[AudioSegment], list[Chapter]]:
        """Split chapters into cached segments (reused) and chapters needing generation."""
        cached: list[AudioSegment] = []
        pending: list[Chapter] = []
        for chapter in project.chapters:
            seg = self._load_cached_segment(chapter, project)
            if seg:
                logger.info("  [-] Skipping (cached): %s", chapter.title)
                cached.append(seg)
            else:
                pending.append(chapter)
        return cached, pending

    def _audio_path(self, chapter: Chapter, project: AudiobookProject) -> Path:
        """Canonical temp path for a chapter's generated audio."""
        return project.temp_dir / f"chap_{chapter.id}.mp3"

    def _load_cached_segment(
        self, chapter: Chapter, project: AudiobookProject
    ) -> AudioSegment | None:
        """Return the already-generated segment for a chapter, or None if not cached."""
        if not self._tracker.is_complete(chapter.id, chapter.checksum):
            return None
        audio_path = self._audio_path(chapter, project)
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return None
        duration = self._tracker.cached_duration_ms(chapter.id)
        if duration <= 0:
            return None
        return AudioSegment(path=audio_path, duration_ms=duration, chapter_id=chapter.id)

    def _record_generated(
        self, chapter: Chapter, audio_path: Path, duration: int
    ) -> AudioSegment | None:
        """Persist a freshly generated chapter and return its segment, or None on failure."""
        if duration <= 0 or not audio_path.exists():
            logger.warning("Failed to generate audio for chapter %s", chapter.id)
            return None
        self._tracker.mark_complete(chapter.id, chapter.checksum, duration)
        return AudioSegment(path=audio_path, duration_ms=duration, chapter_id=chapter.id)
