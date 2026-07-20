"""Build audiobook use case — pure orchestration over ports."""

import logging
from pathlib import Path

from epub_listener.application.commands import BuildAudiobookCommand
from epub_listener.application.ports import (
    ChapterParser,
    MediaAssembler,
    MetadataBuilder,
    ProgressTracker,
    TranscriptEmbedder,
    TTSBatchGenerator,
    TTSJob,
    TTSResult,
    transcript_path_for,
)
from epub_listener.domain.exceptions import EpubListenerError, TTSGenerationError
from epub_listener.domain.models import AudiobookProject, AudioSegment, Chapter

logger = logging.getLogger(__name__)


class BuildAudiobookUseCase:
    """Orchestrates the full EPUB -> audiobook pipeline."""

    def __init__(
        self,
        parser: ChapterParser,
        tts: TTSBatchGenerator,
        assembler: MediaAssembler,
        metadata_builder: MetadataBuilder,
        tracker: ProgressTracker,
        transcript_embedder: TranscriptEmbedder | None = None,
    ) -> None:
        self._parser = parser
        self._tts = tts
        self._assembler = assembler
        self._metadata_builder = metadata_builder
        self._tracker = tracker
        self._transcript_embedder = transcript_embedder

    def execute(self, command: BuildAudiobookCommand) -> Path:
        """Run the full build pipeline.

        Args:
            command: Build command resolved by the composition root. The caller
                owns the temp directory lifecycle.

        Returns:
            Path to the final audiobook MP3.

        Raises:
            EpubListenerError: On any unrecoverable failure.
        """
        output_path = command.output_path
        if output_path.suffix.lower() != ".mp3":
            raise EpubListenerError(f"Output file must end with .mp3 (got '{output_path.suffix}')")
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise EpubListenerError(
                f"Could not prepare output directory {output_path.parent}: {exc}"
            ) from exc

        logger.info("Starting conversion for: %s", command.input_epub)

        logger.info("Step 1/4: Parsing EPUB...")
        chapters = self._parser.parse(command.input_epub)
        if not chapters:
            raise EpubListenerError("No chapters found in the EPUB file.")
        logger.info("Found %d chapters.", len(chapters))

        project = AudiobookProject(
            title=command.title or command.input_epub.stem,
            author=command.author,
            chapters=chapters,
            temp_dir=command.temp_dir,
        )

        logger.info("Step 2/4: Generating chapter audio...")
        segments = self._generate_audio(project, command)
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

        if command.transcript and self._transcript_embedder is not None:
            logger.info("Embedding read-along transcript...")
            embedded = self._transcript_embedder.embed(
                segments,
                chapter_titles,
                command.tts_backend,
                command.generation_key,
                output_path,
            )
            if not embedded:
                logger.warning("Audiobook saved without a read-along transcript.")

        logger.info("Success! Audiobook saved to %s", output_path)
        return output_path

    def _generate_audio(
        self, project: AudiobookProject, command: BuildAudiobookCommand
    ) -> list[AudioSegment]:
        segments_by_chapter, pending = self._partition_pending(project, command)
        if pending:
            chapters_by_id = {chapter.id: chapter for chapter in pending}
            recorded_chapter_ids: set[str] = set()
            jobs = [
                TTSJob(
                    chapter_id=chapter.id,
                    text=chapter.text,
                    output=self._audio_path(chapter, project),
                    voice=command.voice,
                    speed=command.speed,
                    transcript_path=(
                        transcript_path_for(self._audio_path(chapter, project))
                        if command.transcript
                        else None
                    ),
                )
                for chapter in pending
            ]

            def record(result: TTSResult) -> None:
                if result.chapter_id not in chapters_by_id:
                    raise TTSGenerationError(
                        f"TTS provider reported unknown chapter: {result.chapter_id}"
                    )
                if result.chapter_id in recorded_chapter_ids:
                    raise TTSGenerationError(
                        f"TTS provider reported duplicate chapter: {result.chapter_id}"
                    )
                chapter = chapters_by_id[result.chapter_id]
                output = self._audio_path(chapter, project)
                segments_by_chapter[result.chapter_id] = self._record_generated(
                    chapter,
                    output,
                    result,
                    command.generation_key,
                )
                recorded_chapter_ids.add(result.chapter_id)

            self._tts.generate_many(jobs, on_complete=record)
            missing = [chapter.id for chapter in pending if chapter.id not in segments_by_chapter]
            if missing:
                raise TTSGenerationError(
                    f"TTS provider did not produce audio for chapter(s): {', '.join(missing)}"
                )

        return [
            segments_by_chapter[chapter.id]
            for chapter in project.chapters
            if chapter.id in segments_by_chapter
        ]

    def _partition_pending(
        self, project: AudiobookProject, command: BuildAudiobookCommand
    ) -> tuple[dict[str, AudioSegment], list[Chapter]]:
        """Split chapters into cached segments (reused) and chapters needing generation."""
        cached: dict[str, AudioSegment] = {}
        pending: list[Chapter] = []
        for chapter in project.chapters:
            seg = self._load_cached_segment(chapter, project, command)
            if seg:
                logger.info("  [-] Skipping (cached): %s", chapter.title)
                cached[chapter.id] = seg
            else:
                pending.append(chapter)
        return cached, pending

    def _audio_path(self, chapter: Chapter, project: AudiobookProject) -> Path:
        """Canonical temp path for a chapter's generated audio."""
        return project.temp_dir / f"chap_{chapter.id}.mp3"

    def _load_cached_segment(
        self, chapter: Chapter, project: AudiobookProject, command: BuildAudiobookCommand
    ) -> AudioSegment | None:
        """Return the already-generated segment for a chapter, or None if not cached."""
        if not self._tracker.is_complete(chapter.id, chapter.checksum, command.generation_key):
            return None
        audio_path = self._audio_path(chapter, project)
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return None
        # A cached chapter from a build without transcript capture must be
        # regenerated when capture is on, or the final book would embed an
        # incomplete transcript.
        if command.transcript and not transcript_path_for(audio_path).exists():
            logger.info("Chapter %s cached without a transcript, regenerating.", chapter.id)
            return None
        duration = self._tracker.cached_duration_ms(chapter.id)
        if duration <= 0:
            return None
        return AudioSegment(path=audio_path, duration_ms=duration, chapter_id=chapter.id)

    def _record_generated(
        self,
        chapter: Chapter,
        output: Path,
        result: TTSResult,
        generation_key: str,
    ) -> AudioSegment:
        """Persist a freshly generated chapter and return its segment."""
        if result.duration_ms <= 0 or not output.exists() or output.stat().st_size == 0:
            raise TTSGenerationError(f"Failed to generate audio for chapter {chapter.id}")
        self._tracker.mark_complete(
            chapter.id,
            chapter.checksum,
            result.duration_ms,
            generation_key,
        )
        return AudioSegment(
            path=output,
            duration_ms=result.duration_ms,
            chapter_id=chapter.id,
        )
