import asyncio
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest

from epub_listener.application.ports import TTSJob, TTSResult
from epub_listener.domain.exceptions import AudioProbeError, ResumeError, TTSGenerationError
from epub_listener.infrastructure.tts import batch as batch_module
from epub_listener.infrastructure.tts import edge_tts as edge_tts_module
from epub_listener.infrastructure.tts import finalize, kokoro_tts
from epub_listener.infrastructure.tts.batch import (
    SequentialTTSBatchGenerator,
    run_bounded_async,
    run_bounded_futures,
)
from epub_listener.infrastructure.tts.edge_tts import EdgeAsyncTTSBatchGenerator, EdgeTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import (
    KokoroParallelTTSBatchGenerator,
    KokoroTTSProvider,
)


def _jobs(tmp_path: Path, *chapter_ids: str) -> list[TTSJob]:
    return [
        TTSJob(chapter_id, "text", tmp_path / f"{chapter_id}.mp3", None, "+0%")
        for chapter_id in chapter_ids
    ]


class WritingTTSProvider:
    def __init__(self) -> None:
        self.generated: list[str] = []

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        self.generated.append(output.stem)
        output.write_bytes(b"audio")
        return 1000


def _stub_kokoro_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSoundFile:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeSoundFile":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def write(self, samples: object) -> None:
            return None

    class FakeSoundfileModule:
        SoundFile = FakeSoundFile

    monkeypatch.setitem(sys.modules, "soundfile", FakeSoundfileModule())
    monkeypatch.setattr(
        kokoro_tts,
        "_get_pipeline",
        lambda lang: lambda *args, **kwargs: [(None, None, [0.1])],
    )


def test_sequential_batch_stops_scheduling_after_callback_failure(tmp_path: Path) -> None:
    provider = WritingTTSProvider()
    batch = SequentialTTSBatchGenerator(provider)

    def fail_callback(result: TTSResult) -> None:
        raise ResumeError("disk full")

    with pytest.raises(ResumeError, match="disk full"):
        batch.generate_many(_jobs(tmp_path, "first", "later"), fail_callback)

    assert provider.generated == ["first"]


def test_bounded_futures_processes_all_successful_jobs_with_bounded_concurrency(
    tmp_path: Path,
) -> None:
    submitted: list[str] = []
    completed: list[str] = []

    def submit(job: TTSJob) -> Future[int]:
        submitted.append(job.chapter_id)
        future: Future[int] = Future()
        future.set_result(1000)
        return future

    run_bounded_futures(
        _jobs(tmp_path, "one", "two", "three"),
        max_active=1,
        submit=submit,
        on_complete=lambda result: completed.append(result.chapter_id),
    )

    assert submitted == ["one", "two", "three"]
    assert completed == ["one", "two", "three"]


def test_bounded_futures_records_same_wave_successes_before_generation_failure(
    tmp_path: Path,
) -> None:
    submitted: list[str] = []
    completed: list[str] = []

    def submit(job: TTSJob) -> Future[int]:
        submitted.append(job.chapter_id)
        future: Future[int] = Future()
        if job.chapter_id == "bad":
            future.set_exception(TTSGenerationError("bad voice"))
        else:
            future.set_result(1000)
        return future

    with pytest.raises(TTSGenerationError, match="bad voice"):
        run_bounded_futures(
            _jobs(tmp_path, "ok", "bad", "later"),
            max_active=2,
            submit=submit,
            on_complete=lambda result: completed.append(result.chapter_id),
        )

    assert submitted == ["ok", "bad"]
    assert completed == ["ok"]


def test_bounded_futures_stops_callbacks_immediately_after_callback_failure(
    tmp_path: Path,
) -> None:
    submitted: list[str] = []
    callbacks: list[str] = []

    def submit(job: TTSJob) -> Future[int]:
        submitted.append(job.chapter_id)
        future: Future[int] = Future()
        future.set_result(1000)
        return future

    def fail_callback(result: TTSResult) -> None:
        callbacks.append(result.chapter_id)
        raise ResumeError("disk full")

    with pytest.raises(ResumeError, match="disk full"):
        run_bounded_futures(
            _jobs(tmp_path, "first", "second", "later"),
            max_active=2,
            submit=submit,
            on_complete=fail_callback,
        )

    assert submitted == ["first", "second"]
    assert callbacks == ["first"]


def test_bounded_futures_cancels_pending_work_on_keyboard_interrupt(tmp_path: Path) -> None:
    submitted: list[str] = []
    pending_future: Future[int] = Future()
    cancelled_pending: list[int] = []

    def submit(job: TTSJob) -> Future[int]:
        submitted.append(job.chapter_id)
        future: Future[int] = Future()
        if job.chapter_id == "interrupt":
            future.set_exception(KeyboardInterrupt())
            return future
        return pending_future

    def cancel_pending(futures: Sequence[Future[int]]) -> None:
        cancelled_pending.append(len(futures))
        for future in futures:
            future.cancel()

    with pytest.raises(KeyboardInterrupt):
        run_bounded_futures(
            _jobs(tmp_path, "interrupt", "pending", "later"),
            max_active=2,
            submit=submit,
            on_complete=lambda result: None,
            cancel_pending=cancel_pending,
        )

    assert submitted == ["interrupt", "pending"]
    assert cancelled_pending == [1]
    assert pending_future.cancelled()


def test_bounded_futures_times_out_oldest_active_job_even_when_others_complete(
    tmp_path: Path,
) -> None:
    submitted: list[str] = []
    completed: list[str] = []
    cancelled_pending: list[int] = []

    def submit(job: TTSJob) -> Future[int]:
        submitted.append(job.chapter_id)
        future: Future[int] = Future()
        if job.chapter_id != "hung":
            future.set_result(1000)
        return future

    def cancel_pending(futures: Sequence[Future[int]]) -> None:
        cancelled_pending.append(len(futures))
        for future in futures:
            future.cancel()

    with pytest.raises(TTSGenerationError, match="timed out"):
        run_bounded_futures(
            _jobs(tmp_path, "hung", "ok1", "ok2", "ok3"),
            max_active=2,
            submit=submit,
            on_complete=lambda result: completed.append(result.chapter_id),
            cancel_pending=cancel_pending,
            job_timeout_seconds=0.01,
            timeout_error=lambda: TTSGenerationError("timed out"),
        )

    assert submitted == ["hung", "ok1", "ok2", "ok3"]
    assert completed == ["ok1", "ok2", "ok3"]
    assert cancelled_pending == [1]


def test_bounded_futures_interrupt_skips_completed_callbacks(tmp_path: Path) -> None:
    callbacks: list[str] = []

    def submit(job: TTSJob) -> Future[int]:
        future: Future[int] = Future()
        if job.chapter_id == "interrupt":
            future.set_exception(KeyboardInterrupt())
        else:
            future.set_result(1000)
        return future

    with pytest.raises(KeyboardInterrupt):
        run_bounded_futures(
            _jobs(tmp_path, "ok", "interrupt"),
            max_active=2,
            submit=submit,
            on_complete=lambda result: callbacks.append(result.chapter_id),
        )

    assert callbacks == []


def test_bounded_async_uses_same_callback_failure_contract(tmp_path: Path) -> None:
    started: list[str] = []
    callbacks: list[str] = []

    async def start(job: TTSJob) -> int:
        started.append(job.chapter_id)
        return 1000

    def fail_callback(result: TTSResult) -> None:
        callbacks.append(result.chapter_id)
        raise ResumeError("disk full")

    with pytest.raises(ResumeError, match="disk full"):
        asyncio.run(
            run_bounded_async(
                _jobs(tmp_path, "first", "second", "later"),
                max_active=2,
                start=start,
                on_complete=fail_callback,
            )
        )

    assert started == ["first", "second"]
    assert callbacks == ["first"]


def test_bounded_async_records_same_wave_successes_before_generation_failure(
    tmp_path: Path,
) -> None:
    started: list[str] = []
    completed: list[str] = []

    async def start(job: TTSJob) -> int:
        started.append(job.chapter_id)
        if job.chapter_id == "bad":
            raise TTSGenerationError("bad voice")
        return 1000

    with pytest.raises(TTSGenerationError, match="bad voice"):
        asyncio.run(
            run_bounded_async(
                _jobs(tmp_path, "ok", "bad", "later"),
                max_active=2,
                start=start,
                on_complete=lambda result: completed.append(result.chapter_id),
            )
        )

    assert started == ["ok", "bad"]
    assert completed == ["ok"]


def test_bounded_async_interrupt_skips_completed_callbacks(tmp_path: Path) -> None:
    callbacks: list[str] = []

    async def start(job: TTSJob) -> int:
        if job.chapter_id == "interrupt":
            raise KeyboardInterrupt()
        return 1000

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            run_bounded_async(
                _jobs(tmp_path, "ok", "interrupt"),
                max_active=2,
                start=start,
                on_complete=lambda result: callbacks.append(result.chapter_id),
            )
        )

    assert callbacks == []


def test_bounded_async_cleanup_runs_once_after_callback_failure(tmp_path: Path) -> None:
    cancelled: list[str] = []

    async def start(job: TTSJob) -> int:
        if job.chapter_id == "pending":
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append(job.chapter_id)
                raise
        return 1000

    def fail_callback(result: TTSResult) -> None:
        raise ResumeError("disk full")

    with pytest.raises(ResumeError, match="disk full"):
        asyncio.run(
            run_bounded_async(
                _jobs(tmp_path, "done", "pending"),
                max_active=2,
                start=start,
                on_complete=fail_callback,
            )
        )

    assert cancelled == ["pending"]


def test_bounded_async_stops_waiting_for_slow_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(batch_module, "ASYNC_CANCEL_DRAIN_TIMEOUT_SECONDS", 0.01)
    cancelled: list[str] = []
    cleanup_release: asyncio.Future[None] | None = None

    async def start(job: TTSJob) -> int:
        if job.chapter_id == "pending":
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.append(job.chapter_id)
                assert cleanup_release is not None
                await cleanup_release
                return 1000
        return 1000

    def fail_callback(result: TTSResult) -> None:
        raise ResumeError("disk full")

    async def run_case() -> None:
        nonlocal cleanup_release
        cleanup_release = asyncio.get_running_loop().create_future()
        start_time = time.monotonic()
        with pytest.raises(ResumeError, match="disk full"):
            await run_bounded_async(
                _jobs(tmp_path, "done", "pending"),
                max_active=2,
                start=start,
                on_complete=fail_callback,
            )
        assert time.monotonic() - start_time < 0.2
        cleanup_release.set_result(None)
        await asyncio.sleep(0)

    asyncio.run(run_case())
    assert cancelled == ["pending"]


def test_edge_async_batch_does_not_hang_when_task_suppresses_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(batch_module, "ASYNC_CANCEL_DRAIN_TIMEOUT_SECONDS", 0.01)
    cancelled: list[str] = []

    class StubbornEdgeProvider(EdgeTTSProvider):
        async def generate_job(self, job: TTSJob) -> int:
            if job.chapter_id == "pending":
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.append(job.chapter_id)
                    while True:
                        await asyncio.sleep(60)
            return 1000

    def fail_callback(result: TTSResult) -> None:
        raise ResumeError("disk full")

    batch = EdgeAsyncTTSBatchGenerator(StubbornEdgeProvider(), max_concurrent=2)
    start_time = time.monotonic()

    with pytest.raises(ResumeError, match="disk full"):
        batch.generate_many(_jobs(tmp_path, "done", "pending"), fail_callback)

    assert time.monotonic() - start_time < 0.2
    assert cancelled == ["pending"]


class FakeWorkerProcess:
    def __init__(self) -> None:
        self.alive = True
        self.join_timeouts: list[float] = []
        self.terminate_called = False
        self.kill_called = False

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(0.0 if timeout is None else timeout)
        if self.terminate_called:
            self.alive = False

    def terminate(self) -> None:
        self.terminate_called = True

    def kill(self) -> None:
        self.kill_called = True
        self.alive = False


class FakeProcessPoolExecutor:
    instances: list["FakeProcessPoolExecutor"] = []
    failures: set[str] = set()
    interrupts: set[str] = set()
    hangs: set[str] = set()

    def __init__(self, max_workers: int) -> None:
        self.max_workers = max_workers
        self.submitted: list[str] = []
        self.shutdown_calls: list[tuple[bool, bool]] = []
        self.cancel_futures_requested = False
        self.worker = FakeWorkerProcess()
        self._processes = {1: self.worker}
        FakeProcessPoolExecutor.instances.append(self)

    def submit(
        self,
        fn: Callable[[TTSJob, Any], int],
        job: TTSJob,
        cancel_event: Any,
    ) -> Future[int]:
        self.submitted.append(job.chapter_id)
        future: Future[int] = Future()
        if job.chapter_id in self.interrupts:
            future.set_exception(KeyboardInterrupt())
        elif job.chapter_id in self.failures:
            future.set_exception(TTSGenerationError(f"{job.chapter_id} failed"))
        elif job.chapter_id in self.hangs:
            pass
        else:
            future.set_result(1000)
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append((wait, cancel_futures))
        self.cancel_futures_requested = self.cancel_futures_requested or cancel_futures


class FakeCancelEvent:
    def __init__(self) -> None:
        self.was_set = False

    def is_set(self) -> bool:
        return self.was_set

    def set(self) -> None:
        self.was_set = True


class FakeManager:
    instances: list["FakeManager"] = []

    def __init__(self) -> None:
        self.event = FakeCancelEvent()
        self.was_shutdown = False
        FakeManager.instances.append(self)

    def Event(self) -> FakeCancelEvent:
        return self.event

    def shutdown(self) -> None:
        self.was_shutdown = True


def test_kokoro_parallel_batch_cancels_workers_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeProcessPoolExecutor.instances = []
    FakeProcessPoolExecutor.failures = set()
    FakeProcessPoolExecutor.interrupts = {"interrupt"}
    FakeProcessPoolExecutor.hangs = set()
    FakeManager.instances = []
    monkeypatch.setattr(kokoro_tts, "ProcessPoolExecutor", FakeProcessPoolExecutor)
    monkeypatch.setattr(kokoro_tts, "Manager", FakeManager)

    batch = KokoroParallelTTSBatchGenerator(max_workers=1)

    with pytest.raises(KeyboardInterrupt):
        batch.generate_many(_jobs(tmp_path, "interrupt", "later"), lambda result: None)

    executor = FakeProcessPoolExecutor.instances[-1]
    manager = FakeManager.instances[-1]
    assert executor.submitted == ["interrupt"]
    assert executor.shutdown_calls == [(False, True)]
    assert executor.cancel_futures_requested
    assert executor.worker.terminate_called
    assert executor.worker.join_timeouts
    assert manager.event.was_set
    assert manager.was_shutdown


def test_kokoro_parallel_batch_times_out_and_cleans_parent_visible_temp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeProcessPoolExecutor.instances = []
    FakeProcessPoolExecutor.failures = set()
    FakeProcessPoolExecutor.interrupts = set()
    FakeProcessPoolExecutor.hangs = {"hung"}
    FakeManager.instances = []
    monkeypatch.setattr(kokoro_tts, "ProcessPoolExecutor", FakeProcessPoolExecutor)
    monkeypatch.setattr(kokoro_tts, "Manager", FakeManager)
    monkeypatch.setattr(kokoro_tts, "KOKORO_JOB_TIMEOUT_SECONDS", 0.01)

    wav_tmp = tmp_path / ".hung.tmp.wav"
    mp3_tmp = tmp_path / ".hung.tmp.mp3"
    wav_tmp.write_bytes(b"wav")
    mp3_tmp.write_bytes(b"mp3")

    batch = KokoroParallelTTSBatchGenerator(max_workers=1)

    with pytest.raises(TTSGenerationError, match="timed out"):
        batch.generate_many(_jobs(tmp_path, "hung"), lambda result: None)

    executor = FakeProcessPoolExecutor.instances[-1]
    assert executor.shutdown_calls == [(False, True)]
    assert executor.worker.terminate_called
    assert not wav_tmp.exists()
    assert not mp3_tmp.exists()


def test_edge_provider_keeps_existing_output_when_generation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"old")

    async def generate_async(
        self: EdgeTTSProvider,
        text: str,
        output_file: str,
        voice: str,
        rate: str,
    ) -> None:
        Path(output_file).write_bytes(b"new")
        raise ConnectionError("offline")

    monkeypatch.setattr(EdgeTTSProvider, "_generate_async", generate_async)

    with pytest.raises(TTSGenerationError, match="offline"):
        EdgeTTSProvider().generate("text", output, None, "+0%")

    assert output.read_bytes() == b"old"
    assert not (tmp_path / ".chapter.tmp.mp3").exists()


def test_edge_provider_times_out_stalled_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"

    class SlowCommunicate:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def save(self, output_file: str) -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                while True:
                    await asyncio.sleep(60)

    monkeypatch.setattr(edge_tts_module.edge_tts, "Communicate", SlowCommunicate)

    start_time = time.monotonic()
    with pytest.raises(TTSGenerationError, match="timed out"):
        EdgeTTSProvider(timeout_seconds=0.01).generate("text", output, None, "+0%")

    assert time.monotonic() - start_time < 0.2
    assert not output.exists()
    assert not (tmp_path / ".chapter.tmp.mp3").exists()


def test_edge_provider_replaces_output_only_after_valid_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"old")

    async def generate_async(
        self: EdgeTTSProvider,
        text: str,
        output_file: str,
        voice: str,
        rate: str,
    ) -> None:
        Path(output_file).write_bytes(b"new")

    monkeypatch.setattr(EdgeTTSProvider, "_generate_async", generate_async)
    monkeypatch.setattr(finalize, "get_audio_duration_ms", lambda path: 1000)

    assert EdgeTTSProvider().generate("text", output, None, "+0%") == 1000

    assert output.read_bytes() == b"new"
    assert not (tmp_path / ".chapter.tmp.mp3").exists()


def test_commit_generated_mp3_durably_replaces_after_validating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_output = tmp_path / ".chapter.tmp.mp3"
    output = tmp_path / "chapter.mp3"
    tmp_output.write_bytes(b"new")
    events: list[tuple[Path, Path]] = []

    def fake_durably_replace(source: Path, target: Path) -> None:
        events.append((source, target))
        target.write_bytes(source.read_bytes())
        source.unlink()

    monkeypatch.setattr(finalize, "get_audio_duration_ms", lambda path: 1000)
    monkeypatch.setattr(finalize, "durably_replace", fake_durably_replace)

    assert finalize.commit_generated_mp3(tmp_output, output) == 1000

    assert output.read_bytes() == b"new"
    assert not tmp_output.exists()
    assert events == [(tmp_output, output)]


def test_commit_generated_mp3_wraps_probe_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_output = tmp_path / ".chapter.tmp.mp3"
    output = tmp_path / "chapter.mp3"
    tmp_output.write_bytes(b"new")

    def fail_probe(path: Path) -> int:
        raise AudioProbeError("bad probe")

    monkeypatch.setattr(finalize, "get_audio_duration_ms", fail_probe)

    with pytest.raises(TTSGenerationError, match="Could not validate generated MP3"):
        finalize.commit_generated_mp3(tmp_output, output)

    assert not output.exists()
    assert tmp_output.exists()


def test_edge_provider_keeps_existing_output_when_duration_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"old")

    async def generate_async(
        self: EdgeTTSProvider,
        text: str,
        output_file: str,
        voice: str,
        rate: str,
    ) -> None:
        Path(output_file).write_bytes(b"new")

    monkeypatch.setattr(EdgeTTSProvider, "_generate_async", generate_async)
    monkeypatch.setattr(finalize, "get_audio_duration_ms", lambda path: 0)

    with pytest.raises(TTSGenerationError, match="invalid duration"):
        EdgeTTSProvider().generate("text", output, None, "+0%")

    assert output.read_bytes() == b"old"
    assert not (tmp_path / ".chapter.tmp.mp3").exists()


def test_edge_provider_preserves_finalizer_error_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"

    async def generate_async(
        self: EdgeTTSProvider,
        text: str,
        output_file: str,
        voice: str,
        rate: str,
    ) -> None:
        Path(output_file).write_bytes(b"new")

    monkeypatch.setattr(EdgeTTSProvider, "_generate_async", generate_async)
    monkeypatch.setattr(finalize, "get_audio_duration_ms", lambda path: 0)

    with pytest.raises(TTSGenerationError, match="invalid duration") as exc_info:
        EdgeTTSProvider().generate("text", output, None, "+0%")

    assert "Edge-TTS error" not in str(exc_info.value)


def test_kokoro_provider_keeps_existing_output_when_encoding_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"old")

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        tmp_output = tmp_path / ".chapter.tmp.mp3"
        tmp_output.write_bytes(b"new")
        raise TTSGenerationError("encode failed")

    _stub_kokoro_generation(tmp_path, monkeypatch)
    monkeypatch.setattr(kokoro_tts, "run_ffmpeg", fake_run_ffmpeg)

    with pytest.raises(TTSGenerationError, match="encode failed"):
        KokoroTTSProvider().generate("text", output, "af_heart", "+0%")

    assert output.read_bytes() == b"old"
    assert not (tmp_path / ".chapter.tmp.mp3").exists()


def test_kokoro_provider_keeps_existing_output_when_duration_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"old")

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        (tmp_path / ".chapter.tmp.mp3").write_bytes(b"new")

    monkeypatch.setattr(kokoro_tts, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(finalize, "get_audio_duration_ms", lambda path: 0)
    _stub_kokoro_generation(tmp_path, monkeypatch)

    with pytest.raises(TTSGenerationError, match="invalid duration"):
        KokoroTTSProvider().generate("text", output, "af_heart", "+0%")

    assert output.read_bytes() == b"old"
    assert not (tmp_path / ".chapter.tmp.mp3").exists()


def test_kokoro_provider_keeps_existing_output_when_cancelled_after_encode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "chapter.mp3"
    output.write_bytes(b"old")

    class CancelAfterEncode:
        def __init__(self) -> None:
            self.cancelled = False

        def __call__(self) -> bool:
            return self.cancelled

        def set(self) -> None:
            self.cancelled = True

    cancel = CancelAfterEncode()

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        should_cancel = kwargs["should_cancel"]
        assert callable(should_cancel)
        assert not should_cancel()
        (tmp_path / ".chapter.tmp.mp3").write_bytes(b"new")
        cancel.set()

    monkeypatch.setattr(kokoro_tts, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(kokoro_tts, "_cancelled", lambda event: cancel())
    monkeypatch.setattr(finalize, "get_audio_duration_ms", lambda path: 1000)
    _stub_kokoro_generation(tmp_path, monkeypatch)

    with pytest.raises(TTSGenerationError, match="cancelled"):
        KokoroTTSProvider().generate("text", output, "af_heart", "+0%")

    assert output.read_bytes() == b"old"
    assert not (tmp_path / ".chapter.tmp.mp3").exists()
