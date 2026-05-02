"""Build audiobook use case — pure orchestration, zero external deps."""

import asyncio
import logging
import tempfile
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
from epub_listener.infrastructure.utils.file_sanitizer import FileSanitizer

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
        sanitizer: FileSanitizer | None = None,
    ) -> None:
        self._parser = parser
        self._tts = tts
        self._assembler = assembler
        self._metadata_builder = metadata_builder
        self._tracker = tracker
        self._sanitizer = sanitizer or FileSanitizer()

    def execute(self, settings: Settings) -> Path:
        """Run the full build pipeline.

        Returns:
            Path to the final audiobook MP3.

        Raises:
            EpubListenerError: On any unrecoverable failure.
        """
        output_path = settings.resolve_output_path()
        if output_path.suffix.lower() != ".mp3":
            raise EpubListenerError(f"Output file must end with .mp3 (got '{output_path.suffix}')")

        logger.info("Starting conversion for: %s", settings.input_epub)

        # Parse EPUB
        logger.info("Step 1/4: Parsing EPUB...")
        chapters = self._parser.parse(settings.input_epub)
        if not chapters:
            raise EpubListenerError("No chapters found in the EPUB file.")
        logger.info("Found %d chapters.", len(chapters))

        book_title = settings.input_epub.stem
        project = AudiobookProject(
            title=book_title,
            author=settings.author,
            chapters=chapters,
            output_path=output_path,
            temp_dir=self._resolve_temp_dir(settings),
        )

        # Generate audio
        logger.info("Step 2/4: Generating chapter audio...")
        segments = self._generate_audio(project, settings)
        if not segments:
            raise EpubListenerError("Failed to process any valid chapters.")

        # Build metadata
        logger.info("Step 3/4: Compiling chapter metadata...")
        meta_path = project.temp_dir / "ffmetadata.txt"
        chapter_titles = {ch.id: ch.title for ch in project.chapters}
        self._metadata_builder.build(
            segments,
            chapter_titles,
            project.title,
            project.author,
            meta_path,
        )

        # Assemble final
        logger.info("Step 4/4: Exporting final audiobook: %s", output_path)
        success = self._assembler.assemble(segments, meta_path, output_path)
        if not success:
            raise EpubListenerError("Final audio assembly failed.")

        logger.info("Success! Audiobook saved to %s", output_path)
        return output_path

    def _resolve_temp_dir(self, settings: Settings) -> Path:
        if settings.resume_dir and settings.resume_dir.exists():
            logger.info("Resuming from: %s", settings.resume_dir)
            return settings.resume_dir
        return Path(tempfile.mkdtemp(prefix="epub_audiobook_"))

    def _generate_audio(self, project: AudiobookProject, settings: Settings) -> list[AudioSegment]:
        strategy = settings.concurrency
        provider_strategy = self._tts.supports_concurrency()

        # Fall back to sequential if user asked for unsupported concurrency
        if strategy == "parallel" and provider_strategy != "parallel":
            logger.warning("Provider does not support parallel, falling back to sequential.")
            strategy = "sequential"
        elif strategy == "async" and provider_strategy != "async":
            logger.warning("Provider does not support async, falling back to sequential.")
            strategy = "sequential"

        if strategy == "async":
            return self._generate_async(project, settings)
        elif strategy == "parallel":
            return self._generate_parallel(project, settings)
        return self._generate_sequential(project, settings)

    def _generate_sequential(
        self, project: AudiobookProject, settings: Settings
    ) -> list[AudioSegment]:
        segments: list[AudioSegment] = []
        for chapter in project.chapters:
            seg = self._process_chapter(chapter, project, settings)
            if seg:
                segments.append(seg)
        return segments

    def _generate_async(self, project: AudiobookProject, settings: Settings) -> list[AudioSegment]:
        from epub_listener.infrastructure.tts.edge_tts import EdgeTTSProvider

        if not isinstance(self._tts, EdgeTTSProvider):
            logger.warning("Async concurrency only supported for EdgeTTS; falling back.")
            return self._generate_sequential(project, settings)

        async def _run() -> list[AudioSegment]:
            segments: list[AudioSegment] = []
            jobs: list[tuple[str, Path, str | None, str]] = []
            chapter_map: list[Chapter] = []

            for chapter in project.chapters:
                if self._tracker.is_complete(chapter.id, chapter.checksum):
                    existing = project.temp_dir / f"chap_{chapter.id}.mp3"
                    if existing.exists():
                        from epub_listener.infrastructure.utils.audio_probe import (
                            get_audio_duration_ms,
                        )

                        duration = get_audio_duration_ms(existing)
                        segments.append(
                            AudioSegment(path=existing, duration_ms=duration, chapter_id=chapter.id)
                        )
                        continue
                audio_path = project.temp_dir / f"chap_{chapter.id}.mp3"
                jobs.append((chapter.text, audio_path, settings.voice, settings.speed))
                chapter_map.append(chapter)

            if jobs:
                durations = await self._tts.generate_many(jobs)
                for chapter, duration, (_, audio_path, _, _) in zip(
                    chapter_map, durations, jobs, strict=True
                ):
                    if duration > 0 and audio_path.exists():
                        self._tracker.mark_complete(chapter.id, chapter.checksum)
                        segments.append(
                            AudioSegment(
                                path=audio_path, duration_ms=duration, chapter_id=chapter.id
                            )
                        )
                    else:
                        logger.warning("Failed to generate audio for chapter %s", chapter.id)

            return segments

        return asyncio.run(_run())

    def _generate_parallel(
        self, project: AudiobookProject, settings: Settings
    ) -> list[AudioSegment]:
        segments: list[AudioSegment] = []
        pending_jobs: list[tuple[Chapter, Path]] = []

        for chapter in project.chapters:
            if self._tracker.is_complete(chapter.id, chapter.checksum):
                existing = project.temp_dir / f"chap_{chapter.id}.mp3"
                if existing.exists():
                    from epub_listener.infrastructure.utils.audio_probe import (
                        get_audio_duration_ms,
                    )

                    duration = get_audio_duration_ms(existing)
                    segments.append(
                        AudioSegment(path=existing, duration_ms=duration, chapter_id=chapter.id)
                    )
                    continue
            audio_path = project.temp_dir / f"chap_{chapter.id}.mp3"
            pending_jobs.append((chapter, audio_path))

        if pending_jobs:
            with ProcessPoolExecutor(max_workers=settings.max_workers) as executor:
                futures = {
                    executor.submit(
                        self._tts.generate,
                        chapter.text,
                        audio_path,
                        settings.kokoro_voice if settings.use_kokoro else settings.voice,
                        settings.speed,
                    ): (chapter, audio_path)
                    for chapter, audio_path in pending_jobs
                }
                for future in futures:
                    chapter, audio_path = futures[future]
                    try:
                        duration = future.result()
                        if duration > 0 and audio_path.exists():
                            self._tracker.mark_complete(chapter.id, chapter.checksum)
                            segments.append(
                                AudioSegment(
                                    path=audio_path, duration_ms=duration, chapter_id=chapter.id
                                )
                            )
                        else:
                            logger.warning("Failed to generate audio for chapter %s", chapter.id)
                    except Exception as exc:
                        logger.error("Exception generating chapter %s: %s", chapter.id, exc)

        return segments

    def _process_chapter(
        self, chapter: Chapter, project: AudiobookProject, settings: Settings
    ) -> AudioSegment | None:
        audio_path = project.temp_dir / f"chap_{chapter.id}.mp3"

        if self._tracker.is_complete(chapter.id, chapter.checksum):
            logger.info("  [-] Skipping (cached): %s", chapter.title)
            if audio_path.exists():
                from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms

                duration = get_audio_duration_ms(audio_path)
                return AudioSegment(path=audio_path, duration_ms=duration, chapter_id=chapter.id)

        logger.info("  [a] Generating: %s", chapter.title)
        voice = settings.kokoro_voice if settings.use_kokoro else settings.voice
        duration = self._tts.generate(chapter.text, audio_path, voice, settings.speed)
        if duration <= 0:
            logger.warning("  [!] Failed: %s", chapter.title)
            return None

        self._tracker.mark_complete(chapter.id, chapter.checksum)
        return AudioSegment(path=audio_path, duration_ms=duration, chapter_id=chapter.id)
