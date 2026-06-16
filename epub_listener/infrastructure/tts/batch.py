"""Shared TTS batch execution adapters."""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, wait
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, TypeAlias, TypeVar

from epub_listener.application.ports import (
    GenerationCallback,
    TTSJob,
    TTSResult,
)
from epub_listener.infrastructure.tts.ports import TTSProvider

SubmitJob: TypeAlias = Callable[[TTSJob], Future[int]]
CancelPending: TypeAlias = Callable[[Sequence[Future[int]]], None]
MapFutureError: TypeAlias = Callable[[TTSJob, BaseException], BaseException]
StartAsyncJob: TypeAlias = Callable[[TTSJob], Coroutine[Any, Any, int]]
TimeoutErrorFactory: TypeAlias = Callable[[], BaseException]
T = TypeVar("T")

logger = logging.getLogger(__name__)

ASYNC_CANCEL_DRAIN_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class _SubmittedJob:
    index: int
    job: TTSJob
    deadline: float | None = None


def _tts_result(submitted: _SubmittedJob, duration_ms: int) -> TTSResult:
    return TTSResult(submitted.job.chapter_id, duration_ms)


def _completion_error(
    completed: Sequence[TTSResult],
    first_error: BaseException | None,
    on_complete: GenerationCallback,
) -> BaseException | None:
    """Run serial completion callbacks and return the error that should abort the batch."""
    for result in completed:
        try:
            on_complete(result)
        except BaseException as exc:
            return exc
    return first_error


def run_async_safely(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async operation without asyncio.run's unbounded pending-task drain."""
    loop = asyncio.new_event_loop()
    loop.set_exception_handler(_handle_loop_exception)
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        _cancel_remaining_tasks(loop)
        asyncio.set_event_loop(None)
        loop.close()


def _cancel_remaining_tasks(loop: asyncio.AbstractEventLoop) -> None:
    pending = tuple(asyncio.all_tasks(loop))
    if not pending:
        return
    for task in pending:
        task.cancel()
    done, undrained = loop.run_until_complete(
        asyncio.wait(pending, timeout=ASYNC_CANCEL_DRAIN_TIMEOUT_SECONDS)
    )
    for task in done:
        with suppress(BaseException):
            task.exception()
    if undrained:
        logger.warning("Abandoned %d non-cancelling async TTS task(s)", len(undrained))


def _handle_loop_exception(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    if context.get("message") == "Task was destroyed but it is pending!":
        logger.warning("Destroyed pending async TTS task during bounded cleanup")
        return
    loop.default_exception_handler(context)


class SequentialTTSBatchGenerator:
    """Run TTS jobs one at a time through the single-job provider port."""

    def __init__(self, provider: TTSProvider) -> None:
        self._provider = provider

    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        for job in jobs:
            duration_ms = self._provider.generate(job.text, job.output, job.voice, job.speed)
            on_complete(TTSResult(job.chapter_id, duration_ms))


def run_bounded_futures(
    jobs: Sequence[TTSJob],
    *,
    max_active: int,
    submit: SubmitJob,
    on_complete: GenerationCallback,
    cancel_pending: CancelPending | None = None,
    map_error: MapFutureError | None = None,
    job_timeout_seconds: float | None = None,
    timeout_error: TimeoutErrorFactory | None = None,
) -> None:
    """Run jobs through futures with deterministic per-completion callback order."""
    if not jobs:
        return

    job_iter = enumerate(jobs)
    futures: dict[Future[int], _SubmittedJob] = {}
    stopped = False

    def submit_next() -> None:
        try:
            index, job = next(job_iter)
        except StopIteration:
            return
        deadline = (
            time.monotonic() + job_timeout_seconds if job_timeout_seconds is not None else None
        )
        futures[submit(job)] = _SubmittedJob(index, job, deadline)

    def stop_pending() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        pending = tuple(futures)
        if cancel_pending:
            cancel_pending(pending)
        else:
            for future in pending:
                future.cancel()

    def timed_out_jobs() -> list[TTSJob]:
        now = time.monotonic()
        return [
            submitted.job
            for future, submitted in futures.items()
            if not future.done() and submitted.deadline is not None and submitted.deadline <= now
        ]

    def next_wait_timeout() -> float | None:
        deadlines = [
            submitted.deadline for submitted in futures.values() if submitted.deadline is not None
        ]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - time.monotonic())

    def raise_timeout() -> None:
        stop_pending()
        if timeout_error:
            raise timeout_error()
        raise TimeoutError("Future batch timed out")

    try:
        for _ in range(min(max(1, max_active), len(jobs))):
            submit_next()

        while futures:
            if timed_out_jobs():
                raise_timeout()
            done, _ = wait(
                futures,
                timeout=next_wait_timeout(),
                return_when=FIRST_COMPLETED,
            )
            if not done:
                raise_timeout()
            done.update(future for future in futures if future.done())
            completed: list[TTSResult] = []
            first_error: BaseException | None = None

            for future in sorted(done, key=lambda item: futures[item].index):
                submitted = futures.pop(future)
                try:
                    duration_ms = future.result()
                    completed.append(_tts_result(submitted, duration_ms))
                except Exception as exc:
                    mapped = map_error(submitted.job, exc) if map_error else exc
                    first_error = first_error or mapped
                except BaseException:
                    stop_pending()
                    raise

            error = _completion_error(completed, first_error, on_complete)
            if error:
                stop_pending()
                raise error

            for _ in completed:
                submit_next()
    except BaseException:
        if futures:
            stop_pending()
        raise


async def run_bounded_async(
    jobs: Sequence[TTSJob],
    *,
    max_active: int,
    start: StartAsyncJob,
    on_complete: GenerationCallback,
) -> None:
    """Run async TTS jobs with deterministic per-completion callback order."""
    if not jobs:
        return

    job_iter = enumerate(jobs)
    tasks: dict[asyncio.Task[int], _SubmittedJob] = {}
    stopped = False

    def submit_next() -> None:
        try:
            index, job = next(job_iter)
        except StopIteration:
            return
        tasks[asyncio.create_task(start(job))] = _SubmittedJob(index, job)

    async def stop_pending() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        pending = tuple(tasks)
        for task in pending:
            task.cancel()
        if pending:
            done, undrained = await asyncio.wait(
                pending,
                timeout=ASYNC_CANCEL_DRAIN_TIMEOUT_SECONDS,
            )
            for task in done:
                with suppress(BaseException):
                    task.exception()
            if undrained:
                logger.warning(
                    "Timed out waiting for %d async TTS task(s) to cancel", len(undrained)
                )
        tasks.clear()

    try:
        for _ in range(min(max(1, max_active), len(jobs))):
            submit_next()

        while tasks:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            done.update(task for task in tasks if task.done())
            completed: list[TTSResult] = []
            first_error: BaseException | None = None

            for task in sorted(done, key=lambda item: tasks[item].index):
                submitted = tasks.pop(task)
                try:
                    duration_ms = task.result()
                    completed.append(_tts_result(submitted, duration_ms))
                except Exception as exc:
                    first_error = first_error or exc
                except BaseException:
                    await stop_pending()
                    raise

            error = _completion_error(completed, first_error, on_complete)
            if error:
                await stop_pending()
                raise error

            for _ in completed:
                submit_next()
    except BaseException:
        if tasks:
            await stop_pending()
        raise
