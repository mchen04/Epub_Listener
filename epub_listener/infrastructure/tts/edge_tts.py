"""Edge-TTS provider with async concurrency support."""

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

import edge_tts

from epub_listener.application.ports import (
    GenerationCallback,
    TTSJob,
)
from epub_listener.domain.alignment import RawWordCue
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.infrastructure.tts.base import normalize_edge_speed
from epub_listener.infrastructure.tts.batch import run_async_safely, run_bounded_async
from epub_listener.infrastructure.tts.finalize import commit_generated_mp3
from epub_listener.infrastructure.tts.ports import TTSProvider
from epub_listener.infrastructure.tts.transcript_capture import capture_chapter_transcript

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-AriaNeural"
DEFAULT_EDGE_TTS_TIMEOUT_SECONDS = 300

# WordBoundary offsets/durations are reported in 100-nanosecond ticks.
_TICKS_PER_MS = 10_000
# Measured against acoustic speech onsets in the delivered MP3 audio, Edge's
# WordBoundary offsets run consistently ~100 ms early (median -106 ms over a
# 24-word calibration set; see docs/evidence/read-along-ledger.md).
_BOUNDARY_LEAD_CORRECTION_MS = 100


def _boundary_cues(boundaries: list[dict]) -> list[RawWordCue]:
    cues: list[RawWordCue] = []
    for boundary in boundaries:
        text = str(boundary.get("text") or "").strip()
        offset = boundary.get("offset")
        duration = boundary.get("duration")
        if not text or not isinstance(offset, int) or not isinstance(duration, int):
            continue
        start_ms = max(0, round(offset / _TICKS_PER_MS) + _BOUNDARY_LEAD_CORRECTION_MS)
        end_ms = max(
            start_ms,
            round((offset + max(0, duration)) / _TICKS_PER_MS) + _BOUNDARY_LEAD_CORRECTION_MS,
        )
        cues.append(RawWordCue(text, start_ms, end_ms))
    return cues


class EdgeTTSProvider(TTSProvider):
    """Generates audio using Microsoft Edge TTS (cloud/Azure voices)."""

    def __init__(self, timeout_seconds: float = DEFAULT_EDGE_TTS_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate audio and return duration in ms."""
        return self.run_job(TTSJob("_single", text, output, voice, speed))

    def run_job(self, job: TTSJob) -> int:
        """Synchronous single-job entry point used by the sequential batch."""
        return run_async_safely(self.generate_job(job))

    async def generate_job(self, job: TTSJob) -> int:
        voice = job.voice or DEFAULT_VOICE
        rate = normalize_edge_speed(job.speed)
        tmp_output = job.output.with_name(f".{job.output.stem}.tmp{job.output.suffix}")
        try:
            tmp_output.unlink(missing_ok=True)
            boundaries = await self._generate_async(job.text, str(tmp_output), voice, rate)
            if job.transcript_path is not None:
                capture_chapter_transcript(
                    job.transcript_path,
                    job.chapter_id,
                    "edge",
                    job.text,
                    _boundary_cues(boundaries),
                    [],
                )
            return commit_generated_mp3(tmp_output, job.output)
        except TimeoutError as exc:
            logger.error("Edge-TTS timed out after %ss for %s", self.timeout_seconds, job.output)
            raise TTSGenerationError(
                f"Edge-TTS timed out after {self.timeout_seconds:g}s for {job.output}"
            ) from exc
        except ConnectionError as exc:
            logger.error("Edge-TTS connection error for %s: %s", job.output, exc)
            raise TTSGenerationError(f"Edge-TTS connection error: {exc}") from exc
        except OSError as exc:
            logger.error("Edge-TTS OS error for %s: %s", job.output, exc)
            raise TTSGenerationError(f"Edge-TTS OS error: {exc}") from exc
        except TTSGenerationError:
            raise
        except Exception as exc:
            logger.error("Edge-TTS error for %s: %s", job.output, exc)
            raise TTSGenerationError(f"Edge-TTS error: {exc}") from exc
        finally:
            tmp_output.unlink(missing_ok=True)

    async def _generate_async(
        self,
        text: str,
        output_file: str,
        voice: str,
        rate: str,
    ) -> list[dict]:
        """Stream audio to disk while capturing WordBoundary metadata.

        ``Communicate.save`` writes the same audio chunks but discards the
        metadata stream, so word timings must be captured here.
        """
        communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        boundaries: list[dict] = []

        async def stream_to_file() -> None:
            with open(output_file, "wb") as handle:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        handle.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        boundaries.append(chunk)

        stream_task = asyncio.create_task(stream_to_file())
        done, _ = await asyncio.wait((stream_task,), timeout=self.timeout_seconds)
        if not done:
            stream_task.cancel()
            raise TimeoutError
        await stream_task
        return boundaries


class EdgeAsyncTTSBatchGenerator:
    """Runs Edge jobs concurrently through the shared async batch policy."""

    def __init__(self, provider: EdgeTTSProvider, max_concurrent: int = 5) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self.provider = provider

    def generate_many(
        self,
        jobs: Sequence[TTSJob],
        on_complete: GenerationCallback,
    ) -> None:
        run_async_safely(
            run_bounded_async(
                jobs,
                max_active=self.max_concurrent,
                start=self.provider.generate_job,
                on_complete=on_complete,
            )
        )
