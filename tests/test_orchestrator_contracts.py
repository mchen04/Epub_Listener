from collections.abc import Sequence
from pathlib import Path

import pytest

from epub_listener.application.commands import BuildAudiobookCommand
from epub_listener.application.orchestrator import BuildAudiobookUseCase
from epub_listener.application.ports import (
    GenerationCallback,
    TTSJob,
    TTSResult,
    transcript_path_for,
)
from epub_listener.domain.exceptions import EpubListenerError, TTSGenerationError
from epub_listener.domain.models import AudioSegment, Chapter
from epub_listener.domain.transcript import SentenceCue
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker
from epub_listener.infrastructure.tts.transcript_capture import write_chapter_transcript


class StaticParser:
    def __init__(self, chapters: list[Chapter]) -> None:
        self._chapters = chapters

    def parse(self, epub_path: Path) -> list[Chapter]:
        return self._chapters


class CapturingAssembler:
    def __init__(self) -> None:
        self.chapter_ids: list[str] = []
        self.segments: list[AudioSegment] = []

    def assemble(
        self,
        segments: list[AudioSegment],
        metadata_path: Path,
        output: Path,
    ) -> None:
        self.segments = list(segments)
        self.chapter_ids = [segment.chapter_id for segment in segments]
        output.write_bytes(b"mp3")


class MetadataWriter:
    def __init__(self) -> None:
        self.book_title: str | None = None
        self.book_author: str | None = None

    def build(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        book_title: str,
        book_author: str,
        output: Path,
    ) -> None:
        self.book_title = book_title
        self.book_author = book_author
        output.write_text("metadata", encoding="utf-8")


def _honor_transcript_contract(job: TTSJob) -> None:
    """Real providers persist a chapter transcript before reporting a job done."""
    if job.transcript_path is not None:
        write_chapter_transcript(
            job.transcript_path,
            job.chapter_id,
            "fake",
            [SentenceCue("Stub sentence.", 0, 1000, ())],
        )


class ReversingBatchGenerator:
    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        for job in reversed(jobs):
            job.output.write_bytes(b"audio")
            result = TTSResult(job.chapter_id, 1000)
            on_complete(result)


class PartialFailingBatchGenerator:
    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        first = jobs[0]
        first.output.write_bytes(b"audio")
        _honor_transcript_contract(first)
        result = TTSResult(first.chapter_id, 1000)
        on_complete(result)
        raise TTSGenerationError("boom")


class MissingCallbackBatchGenerator:
    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        return None


class UnknownChapterBatchGenerator:
    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        jobs[0].output.write_bytes(b"audio")
        on_complete(TTSResult("unknown", 1000))


class DuplicateChapterBatchGenerator:
    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        jobs[0].output.write_bytes(b"audio")
        on_complete(TTSResult(jobs[0].chapter_id, 1000))
        on_complete(TTSResult(jobs[0].chapter_id, 1000))


class InvalidOutputBatchGenerator:
    def __init__(self, payload: bytes | None, duration_ms: int) -> None:
        self._payload = payload
        self._duration_ms = duration_ms

    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        job = jobs[0]
        if self._payload is not None:
            job.output.write_bytes(self._payload)
        on_complete(TTSResult(job.chapter_id, self._duration_ms))


class FailingOnGenerateBatchGenerator:
    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        raise AssertionError("cached chapters should not be regenerated")


class WritingBatchGenerator:
    def __init__(self, duration_ms: int = 3333) -> None:
        self.duration_ms = duration_ms
        self.generated: list[str] = []

    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        for job in jobs:
            self.generated.append(job.chapter_id)
            job.output.write_bytes(b"fresh audio")
            _honor_transcript_contract(job)
            on_complete(TTSResult(job.chapter_id, self.duration_ms))


def _command(tmp_path: Path) -> BuildAudiobookCommand:
    epub_path = tmp_path / "book.epub"
    epub_path.write_bytes(b"epub")
    return BuildAudiobookCommand(
        input_epub=epub_path,
        output_path=tmp_path / "book.mp3",
        author="Author",
        voice=None,
        speed="+0%",
        temp_dir=tmp_path / "work",
    )


def test_use_case_assembles_in_chapter_order_after_out_of_order_completion(tmp_path: Path) -> None:
    chapters = [
        Chapter("0000", "One", "one " * 50),
        Chapter("0001", "Two", "two " * 50),
        Chapter("0002", "Three", "three " * 50),
    ]
    assembler = CapturingAssembler()
    tracker = JsonProgressTracker(tmp_path / "work")
    use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=ReversingBatchGenerator(),
        assembler=assembler,
        metadata_builder=MetadataWriter(),
        tracker=tracker,
    )

    output = use_case.execute(_command(tmp_path))

    assert output.exists()
    assert assembler.chapter_ids == ["0000", "0001", "0002"]
    assert all(tracker.is_complete(chapter.id, chapter.checksum) for chapter in chapters)


def test_use_case_uses_explicit_audiobook_title_for_metadata(tmp_path: Path) -> None:
    chapter = Chapter("0000", "One", "one " * 50)
    metadata = MetadataWriter()
    command = _command(tmp_path)
    command = BuildAudiobookCommand(
        input_epub=command.input_epub,
        output_path=command.output_path,
        author=command.author,
        voice=command.voice,
        speed=command.speed,
        temp_dir=command.temp_dir,
        title="Reverend Insanity",
    )
    use_case = BuildAudiobookUseCase(
        parser=StaticParser([chapter]),
        tts=WritingBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=metadata,
        tracker=JsonProgressTracker(tmp_path / "work"),
    )

    use_case.execute(command)

    assert metadata.book_title == "Reverend Insanity"
    assert metadata.book_author == "Author"


def test_use_case_wraps_output_directory_creation_failure(tmp_path: Path) -> None:
    output_parent = tmp_path / "not-a-directory"
    output_parent.write_text("file", encoding="utf-8")
    command = _command(tmp_path)
    command = BuildAudiobookCommand(
        input_epub=command.input_epub,
        output_path=output_parent / "book.mp3",
        author=command.author,
        voice=command.voice,
        speed=command.speed,
        temp_dir=command.temp_dir,
    )
    use_case = BuildAudiobookUseCase(
        parser=StaticParser([Chapter("0000", "One", "one " * 50)]),
        tts=FailingOnGenerateBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(tmp_path / "work"),
    )

    with pytest.raises(EpubListenerError, match="Could not prepare output directory"):
        use_case.execute(command)


def test_use_case_persists_completed_chapter_before_batch_failure(tmp_path: Path) -> None:
    chapters = [
        Chapter("0000", "One", "one " * 50),
        Chapter("0001", "Two", "two " * 50),
    ]
    assembler = CapturingAssembler()
    tracker = JsonProgressTracker(tmp_path / "work")
    use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=PartialFailingBatchGenerator(),
        assembler=assembler,
        metadata_builder=MetadataWriter(),
        tracker=tracker,
    )

    with pytest.raises(TTSGenerationError):
        use_case.execute(_command(tmp_path))

    assert tracker.is_complete(chapters[0].id, chapters[0].checksum)
    assert not tracker.is_complete(chapters[1].id, chapters[1].checksum)
    assert assembler.chapter_ids == []


def test_use_case_resumes_after_partial_failure_and_only_generates_missing_chapters(
    tmp_path: Path,
) -> None:
    chapters = [
        Chapter("0000", "One", "one " * 50),
        Chapter("0001", "Two", "two " * 50),
    ]
    command = _command(tmp_path)
    work_dir = command.temp_dir
    first_tracker = JsonProgressTracker(work_dir)
    first_use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=PartialFailingBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=first_tracker,
    )

    with pytest.raises(TTSGenerationError):
        first_use_case.execute(command)

    resumed_tts = WritingBatchGenerator(duration_ms=3333)
    assembler = CapturingAssembler()
    resumed_use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=resumed_tts,
        assembler=assembler,
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(work_dir),
    )

    output = resumed_use_case.execute(command)

    assert output.exists()
    assert resumed_tts.generated == ["0001"]
    assert assembler.chapter_ids == ["0000", "0001"]
    assert [
        (segment.path, segment.duration_ms, segment.chapter_id) for segment in assembler.segments
    ] == [
        (work_dir / "chap_0000.mp3", 1000, "0000"),
        (work_dir / "chap_0001.mp3", 3333, "0001"),
    ]


def test_use_case_fresh_resume_reuses_cached_segments_without_tts(tmp_path: Path) -> None:
    chapters = [
        Chapter("0000", "One", "one " * 50),
        Chapter("0001", "Two", "two " * 50),
    ]
    work_dir = tmp_path / "work"
    tracker = JsonProgressTracker(work_dir)
    command = _command(tmp_path)
    for chapter, duration_ms in zip(chapters, (1000, 2000), strict=True):
        audio_path = work_dir / f"chap_{chapter.id}.mp3"
        audio_path.write_bytes(b"audio")
        write_chapter_transcript(
            transcript_path_for(audio_path),
            chapter.id,
            "fake",
            [SentenceCue("Stub sentence.", 0, 1000, ())],
        )
        tracker.mark_complete(chapter.id, chapter.checksum, duration_ms, command.generation_key)

    assembler = CapturingAssembler()
    resumed_tracker = JsonProgressTracker(work_dir)
    use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=FailingOnGenerateBatchGenerator(),
        assembler=assembler,
        metadata_builder=MetadataWriter(),
        tracker=resumed_tracker,
    )

    output = use_case.execute(command)

    assert output.exists()
    assert assembler.chapter_ids == ["0000", "0001"]
    assert [
        (segment.path, segment.duration_ms, segment.chapter_id) for segment in assembler.segments
    ] == [
        (work_dir / "chap_0000.mp3", 1000, "0000"),
        (work_dir / "chap_0001.mp3", 2000, "0001"),
    ]


@pytest.mark.parametrize("payload", [None, b""], ids=["missing-audio", "empty-audio"])
def test_use_case_regenerates_stale_cached_audio(
    tmp_path: Path,
    payload: bytes | None,
) -> None:
    chapter = Chapter("0000", "One", "one " * 50)
    work_dir = tmp_path / "work"
    tracker = JsonProgressTracker(work_dir)
    audio_path = work_dir / "chap_0000.mp3"
    if payload is not None:
        audio_path.write_bytes(payload)
    tracker.mark_complete(chapter.id, chapter.checksum, 1000)

    tts = WritingBatchGenerator(duration_ms=3333)
    assembler = CapturingAssembler()
    use_case = BuildAudiobookUseCase(
        parser=StaticParser([chapter]),
        tts=tts,
        assembler=assembler,
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(work_dir),
    )

    output = use_case.execute(_command(tmp_path))

    assert output.exists()
    assert tts.generated == ["0000"]
    assert assembler.segments[0].duration_ms == 3333
    assert audio_path.read_bytes() == b"fresh audio"


@pytest.mark.parametrize(
    "command_override",
    [
        {"voice": "different-voice"},
        {"speed": "+25%"},
        {"tts_backend": "kokoro"},
    ],
    ids=["voice-change", "speed-change", "backend-change"],
)
def test_use_case_regenerates_when_tts_generation_settings_change(
    tmp_path: Path,
    command_override: dict[str, str],
) -> None:
    chapter = Chapter("0000", "One", "one " * 50)
    work_dir = tmp_path / "work"
    original_command = _command(tmp_path)
    original_key = original_command.generation_key
    tracker = JsonProgressTracker(work_dir)
    audio_path = work_dir / "chap_0000.mp3"
    audio_path.write_bytes(b"old audio")
    tracker.mark_complete(chapter.id, chapter.checksum, 1000, original_key)

    command = BuildAudiobookCommand(
        input_epub=original_command.input_epub,
        output_path=original_command.output_path,
        author=original_command.author,
        voice=command_override.get("voice", original_command.voice),
        speed=command_override.get("speed", original_command.speed),
        temp_dir=original_command.temp_dir,
        tts_backend=command_override.get("tts_backend", original_command.tts_backend),
    )
    tts = WritingBatchGenerator(duration_ms=3333)
    use_case = BuildAudiobookUseCase(
        parser=StaticParser([chapter]),
        tts=tts,
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(work_dir),
    )

    use_case.execute(command)

    assert tts.generated == ["0000"]
    assert JsonProgressTracker(work_dir).is_complete(
        chapter.id,
        chapter.checksum,
        command.generation_key,
    )


def test_use_case_fails_when_provider_omits_a_pending_chapter(tmp_path: Path) -> None:
    chapters = [Chapter("0000", "One", "one " * 50)]
    use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=MissingCallbackBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(tmp_path / "work"),
    )

    with pytest.raises(TTSGenerationError, match="did not produce audio"):
        use_case.execute(_command(tmp_path))


@pytest.mark.parametrize(
    ("payload", "duration_ms"),
    [
        (None, 1000),
        (b"", 1000),
        (b"audio", 0),
    ],
    ids=["missing-output", "empty-output", "zero-duration"],
)
def test_use_case_rejects_invalid_batch_result_before_persisting_progress(
    tmp_path: Path,
    payload: bytes | None,
    duration_ms: int,
) -> None:
    chapters = [Chapter("0000", "One", "one " * 50)]
    assembler = CapturingAssembler()
    tracker = JsonProgressTracker(tmp_path / "work")
    use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=InvalidOutputBatchGenerator(payload, duration_ms),
        assembler=assembler,
        metadata_builder=MetadataWriter(),
        tracker=tracker,
    )

    with pytest.raises(TTSGenerationError, match="Failed to generate audio"):
        use_case.execute(_command(tmp_path))

    assert not tracker.is_complete(chapters[0].id, chapters[0].checksum)
    assert assembler.chapter_ids == []
    assert not (tmp_path / "book.mp3").exists()


def test_use_case_rejects_unknown_batch_result_chapter(tmp_path: Path) -> None:
    chapters = [Chapter("0000", "One", "one " * 50)]
    use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=UnknownChapterBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(tmp_path / "work"),
    )

    with pytest.raises(TTSGenerationError, match="unknown chapter"):
        use_case.execute(_command(tmp_path))


def test_use_case_rejects_duplicate_batch_result_chapter(tmp_path: Path) -> None:
    chapters = [Chapter("0000", "One", "one " * 50)]
    use_case = BuildAudiobookUseCase(
        parser=StaticParser(chapters),
        tts=DuplicateChapterBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(tmp_path / "work"),
    )

    with pytest.raises(TTSGenerationError, match="duplicate chapter"):
        use_case.execute(_command(tmp_path))


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, Path]] = []

    def embed(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        engine: str,
        generation_key: str,
        output: Path,
    ) -> bool:
        self.calls.append(([segment.chapter_id for segment in segments], engine, output))
        return True


def test_use_case_embeds_transcript_after_assembly(tmp_path: Path) -> None:
    chapter = Chapter("0000", "One", "one " * 50)
    embedder = RecordingEmbedder()
    use_case = BuildAudiobookUseCase(
        parser=StaticParser([chapter]),
        tts=WritingBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(tmp_path / "work"),
        transcript_embedder=embedder,
    )

    output = use_case.execute(_command(tmp_path))

    assert embedder.calls == [(["0000"], "edge", output)]


def test_use_case_transcript_flag_off_disables_capture_and_embed(tmp_path: Path) -> None:
    chapter = Chapter("0000", "One", "one " * 50)
    embedder = RecordingEmbedder()

    class JobInspectingGenerator(WritingBatchGenerator):
        captured_jobs: list[TTSJob] = []

        def generate_many(self, jobs, on_complete):
            type(self).captured_jobs = list(jobs)
            super().generate_many(jobs, on_complete)

    epub_path = tmp_path / "book.epub"
    epub_path.write_bytes(b"epub")
    command = BuildAudiobookCommand(
        input_epub=epub_path,
        output_path=tmp_path / "book.mp3",
        author="Author",
        voice=None,
        speed="+0%",
        temp_dir=tmp_path / "work",
        transcript=False,
    )
    use_case = BuildAudiobookUseCase(
        parser=StaticParser([chapter]),
        tts=JobInspectingGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(tmp_path / "work"),
        transcript_embedder=embedder,
    )

    use_case.execute(command)

    assert [job.transcript_path for job in JobInspectingGenerator.captured_jobs] == [None]
    assert embedder.calls == []
    assert not list((tmp_path / "work").glob("*.transcript.json"))


def test_use_case_flag_off_reuses_cache_missing_transcripts(tmp_path: Path) -> None:
    """Books cached before the transcript feature stay resumable with the flag off."""
    chapter = Chapter("0000", "One", "one " * 50)
    work_dir = tmp_path / "work"
    epub_path = tmp_path / "book.epub"
    epub_path.write_bytes(b"epub")
    command = BuildAudiobookCommand(
        input_epub=epub_path,
        output_path=tmp_path / "book.mp3",
        author="Author",
        voice=None,
        speed="+0%",
        temp_dir=work_dir,
        transcript=False,
    )
    tracker = JsonProgressTracker(work_dir)
    audio_path = work_dir / "chap_0000.mp3"
    audio_path.write_bytes(b"audio")
    tracker.mark_complete(chapter.id, chapter.checksum, 1000, command.generation_key)

    use_case = BuildAudiobookUseCase(
        parser=StaticParser([chapter]),
        tts=FailingOnGenerateBatchGenerator(),
        assembler=CapturingAssembler(),
        metadata_builder=MetadataWriter(),
        tracker=JsonProgressTracker(work_dir),
    )

    assert use_case.execute(command).exists()
