"""Kokoro-82M local TTS provider."""

import logging
import os
import time
import warnings
from collections.abc import Sequence
from concurrent.futures import Future, ProcessPoolExecutor
from multiprocessing import Manager
from pathlib import Path
from typing import Any

import numpy as np

from epub_listener.application.ports import (
    GenerationCallback,
    TTSJob,
)
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.infrastructure.tts.base import (
    edge_speed_to_multiplier,
    infer_kokoro_lang_for_voice,
)
from epub_listener.infrastructure.tts.batch import run_bounded_futures
from epub_listener.infrastructure.tts.finalize import commit_generated_mp3
from epub_listener.infrastructure.tts.ports import TTSProvider
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24000
PROCESS_POOL_SHUTDOWN_TIMEOUT_SECONDS = 5.0
PROCESS_POOL_TERMINATE_TIMEOUT_SECONDS = 1.0
KOKORO_JOB_TIMEOUT_SECONDS = 7200.0

_PIPELINES: dict[tuple[str, str], Any] = {}
_WORKER_DEVICE = "cpu"


def _get_pipeline(lang_code: str) -> Any:
    key = (lang_code, _WORKER_DEVICE)
    if key not in _PIPELINES:
        try:
            from kokoro import KModel, KPipeline
        except ImportError as exc:
            raise TTSGenerationError(
                "Kokoro is not installed. Run: pip install kokoro>=0.9.4 soundfile"
            ) from exc
        if _WORKER_DEVICE == "mps":
            try:
                import torch

                if not torch.backends.mps.is_available():
                    raise TTSGenerationError("Kokoro MPS worker requested but MPS is unavailable")
                model = KModel(repo_id="hexgrad/Kokoro-82M").to("mps").eval()
                pipeline = KPipeline(
                    lang_code=lang_code,
                    repo_id="hexgrad/Kokoro-82M",
                    model=model,
                )
            except TTSGenerationError:
                raise
            except Exception as exc:
                raise TTSGenerationError(f"Could not initialize Kokoro on MPS: {exc}") from exc
        else:
            pipeline = KPipeline(
                lang_code=lang_code,
                repo_id="hexgrad/Kokoro-82M",
                device="cpu",
            )
        _PIPELINES[key] = pipeline
    return _PIPELINES[key]


def _initialize_hybrid_worker(counter: Any, lock: Any, torch_threads: int) -> None:
    """Permanently bind one spawned worker to MPS and the other to CPU."""
    global _WORKER_DEVICE
    with lock:
        worker_index = int(counter.value)
        counter.value = worker_index + 1
    _WORKER_DEVICE = "mps" if worker_index == 0 else "cpu"

    # PyTorch 2.12's native MPS STFT is correct but emits a deprecation
    # warning whose text includes the dynamic tensor shape, defeating the
    # default once-per-location filter and flooding long audiobook logs.
    warnings.filterwarnings(
        "ignore",
        message=r"An output with one or more elements was resized since it had shape.*",
        category=UserWarning,
    )

    import torch

    torch.set_num_threads(max(1, torch_threads))
    torch.set_num_interop_threads(1)


def _cancelled(cancel_event: Any | None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def _tmp_output_path(job: TTSJob) -> Path:
    return job.output.with_name(f".{job.output.stem}.tmp{job.output.suffix}")


def _tmp_wav_path(job: TTSJob) -> Path:
    return job.output.with_name(f".{job.output.stem}.tmp.wav")


def _generate_kokoro_job(job: TTSJob, cancel_event: Any | None = None) -> int:
    if _cancelled(cancel_event):
        raise TTSGenerationError("Kokoro batch cancelled")
    voice = job.voice or DEFAULT_VOICE
    lang = infer_kokoro_lang_for_voice(voice)
    tmp_wav_path = _tmp_wav_path(job)
    tmp_output = _tmp_output_path(job)
    try:
        import soundfile as sf
    except ImportError as exc:
        raise TTSGenerationError("soundfile not installed") from exc

    try:
        tmp_wav_path.unlink(missing_ok=True)
        tmp_output.unlink(missing_ok=True)
        pipeline = _get_pipeline(lang)
        speed_float = edge_speed_to_multiplier(job.speed)
        generator = pipeline(job.text, voice=voice, speed=speed_float)

        total_samples = 0
        with sf.SoundFile(
            tmp_wav_path,
            mode="w",
            samplerate=SAMPLE_RATE,
            channels=1,
            format="WAV",
        ) as wav_file:
            for _, _, audio in generator:
                if _cancelled(cancel_event):
                    raise TTSGenerationError("Kokoro batch cancelled")
                samples = np.asarray(audio, dtype=np.float32)
                if samples.ndim != 1:
                    samples = samples.reshape(-1)
                if samples.size == 0:
                    continue
                wav_file.write(samples)
                total_samples += int(samples.shape[0])

        if total_samples <= 0:
            raise TTSGenerationError(f"Kokoro produced no audio for {job.output}")
        if _cancelled(cancel_event):
            raise TTSGenerationError("Kokoro batch cancelled")

        run_ffmpeg(
            "-i",
            tmp_wav_path,
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            tmp_output,
            should_cancel=lambda: _cancelled(cancel_event),
        )
        return commit_generated_mp3(
            tmp_output,
            job.output,
            should_cancel=lambda: _cancelled(cancel_event),
        )
    except TTSGenerationError:
        raise
    except Exception as exc:
        logger.error("Kokoro error for %s: %s", job.output, exc)
        raise TTSGenerationError(f"Kokoro error: {exc}") from exc
    finally:
        tmp_wav_path.unlink(missing_ok=True)
        tmp_output.unlink(missing_ok=True)


def _executor_processes(executor: ProcessPoolExecutor) -> tuple[Any, ...]:
    processes = getattr(executor, "_processes", None)
    if not processes:
        return ()
    if isinstance(processes, dict):
        return tuple(processes.values())
    return tuple(processes)


def _join_processes(processes: Sequence[Any], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    for process in processes:
        join = getattr(process, "join", None)
        if not callable(join):
            continue
        remaining = max(0.0, deadline - time.monotonic())
        join(remaining)


def _signal_live_processes(processes: Sequence[Any], method_name: str) -> None:
    for process in processes:
        is_alive = getattr(process, "is_alive", None)
        signal = getattr(process, method_name, None)
        if callable(is_alive) and callable(signal) and is_alive():
            signal()


def _shutdown_process_pool_now(executor: ProcessPoolExecutor) -> None:
    processes = _executor_processes(executor)
    executor.shutdown(wait=False, cancel_futures=True)
    _join_processes(processes, PROCESS_POOL_SHUTDOWN_TIMEOUT_SECONDS)
    _signal_live_processes(processes, "terminate")
    _join_processes(processes, PROCESS_POOL_TERMINATE_TIMEOUT_SECONDS)
    _signal_live_processes(processes, "kill")
    _join_processes(processes, PROCESS_POOL_TERMINATE_TIMEOUT_SECONDS)


def _cleanup_job_temp_files(jobs: Sequence[TTSJob]) -> None:
    for job in jobs:
        for path in (_tmp_wav_path(job), _tmp_output_path(job)):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temp Kokoro file %s: %s", path, exc)


class KokoroTTSProvider(TTSProvider):
    """Generates audio using local Kokoro-82M inference."""

    @staticmethod
    def generate_job(job: TTSJob, cancel_event: Any | None = None) -> int:
        return _generate_kokoro_job(job, cancel_event)

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate audio and return duration in ms."""
        return self.generate_job(TTSJob("_single", text, output, voice, speed))


class KokoroParallelTTSBatchGenerator:
    """Runs Kokoro jobs in processes through the shared future batch policy."""

    def __init__(self, max_workers: int = 4, *, hybrid_mps: bool = False) -> None:
        self.hybrid_mps = hybrid_mps
        # The tuned hybrid topology is exactly one native-MPS worker and one
        # internally multithreaded CPU worker. Additional model copies reduce
        # throughput on unified-memory Apple Silicon.
        self.max_workers = 2 if hybrid_mps else max(1, max_workers)

    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        if not jobs:
            return

        manager = Manager()
        executor: ProcessPoolExecutor | None = None
        shutdown_early = False

        try:
            cancel_event = manager.Event()
            if self.hybrid_mps:
                device_counter = manager.Value("i", 0)
                device_lock = manager.Lock()
                executor = ProcessPoolExecutor(
                    max_workers=self.max_workers,
                    initializer=_initialize_hybrid_worker,
                    initargs=(device_counter, device_lock, os.cpu_count() or 1),
                )
            else:
                executor = ProcessPoolExecutor(max_workers=self.max_workers)

            def submit(job: TTSJob) -> Future[int]:
                assert executor is not None
                return executor.submit(KokoroTTSProvider.generate_job, job, cancel_event)

            def stop_workers(futures: Sequence[Future[int]]) -> None:
                nonlocal shutdown_early
                shutdown_early = True
                cancel_event.set()
                for pending in futures:
                    pending.cancel()
                assert executor is not None
                _shutdown_process_pool_now(executor)
                _cleanup_job_temp_files(jobs)

            def map_error(job: TTSJob, exc: BaseException) -> BaseException:
                if not isinstance(exc, Exception):
                    return exc
                if isinstance(exc, TTSGenerationError):
                    return exc
                return TTSGenerationError(f"Kokoro failed for {job.chapter_id}: {exc}")

            def timeout_error() -> TTSGenerationError:
                return TTSGenerationError(f"Kokoro timed out after {KOKORO_JOB_TIMEOUT_SECONDS:g}s")

            run_bounded_futures(
                jobs,
                max_active=self.max_workers,
                submit=submit,
                on_complete=on_complete,
                cancel_pending=stop_workers,
                map_error=map_error,
                job_timeout_seconds=KOKORO_JOB_TIMEOUT_SECONDS,
                timeout_error=timeout_error,
            )
        finally:
            if executor is not None and not shutdown_early:
                executor.shutdown(wait=True)
            manager.shutdown()
