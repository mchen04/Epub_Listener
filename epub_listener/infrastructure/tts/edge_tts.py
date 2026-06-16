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
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.infrastructure.tts.base import normalize_edge_speed
from epub_listener.infrastructure.tts.batch import run_async_safely, run_bounded_async
from epub_listener.infrastructure.tts.finalize import commit_generated_mp3
from epub_listener.infrastructure.tts.ports import TTSProvider

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-AriaNeural"
DEFAULT_EDGE_TTS_TIMEOUT_SECONDS = 300


class EdgeTTSProvider(TTSProvider):
    """Generates audio using Microsoft Edge TTS (cloud/Azure voices)."""

    def __init__(self, timeout_seconds: float = DEFAULT_EDGE_TTS_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate audio and return duration in ms."""
        return run_async_safely(self.generate_job(TTSJob("_single", text, output, voice, speed)))

    async def generate_job(self, job: TTSJob) -> int:
        voice = job.voice or DEFAULT_VOICE
        rate = normalize_edge_speed(job.speed)
        tmp_output = job.output.with_name(f".{job.output.stem}.tmp{job.output.suffix}")
        try:
            tmp_output.unlink(missing_ok=True)
            await self._generate_async(job.text, str(tmp_output), voice, rate)
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
    ) -> None:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        save_task = asyncio.create_task(communicate.save(output_file))
        done, _ = await asyncio.wait((save_task,), timeout=self.timeout_seconds)
        if not done:
            save_task.cancel()
            raise TimeoutError
        await save_task


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
