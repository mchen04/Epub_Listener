"""Edge-TTS provider with async concurrency support."""

import asyncio
import logging
from pathlib import Path

import edge_tts

from epub_listener.application.ports import ConcurrencyStrategy, TTSProvider
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.infrastructure.tts.base import normalize_edge_speed
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-AriaNeural"


class EdgeTTSProvider(TTSProvider):
    """Generates audio using Microsoft Edge TTS (cloud/Azure voices)."""

    def __init__(self, max_concurrent: int = 5) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def supports_concurrency(self) -> ConcurrencyStrategy:
        return "async"

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate audio and return duration in ms."""
        voice = voice or DEFAULT_VOICE
        rate = normalize_edge_speed(speed)
        try:
            asyncio.run(self._generate_async(text, str(output), voice, rate))
            if output.exists():
                return get_audio_duration_ms(output)
        except ConnectionError as exc:
            logger.error("Edge-TTS connection error for %s: %s", output, exc)
            raise TTSGenerationError(f"Edge-TTS connection error: {exc}") from exc
        except OSError as exc:
            logger.error("Edge-TTS OS error for %s: %s", output, exc)
            raise TTSGenerationError(f"Edge-TTS OS error: {exc}") from exc
        except Exception as exc:
            logger.error("Edge-TTS error for %s: %s", output, exc)
            raise TTSGenerationError(f"Edge-TTS error: {exc}") from exc
        return 0

    async def _generate_async(self, text: str, output_file: str, voice: str, rate: str) -> None:
        async with self._semaphore:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(output_file)

    async def generate_many(
        self,
        jobs: list[tuple[str, Path, str | None, str]],
    ) -> list[int]:
        """Concurrent generation for multiple chapters.

        Args:
            jobs: List of (text, output, voice, speed) tuples.

        Returns:
            List of durations in ms (0 for failures).
        """
        tasks = [
            asyncio.create_task(self._generate_one_safe(text, out, voice, speed))
            for text, out, voice, speed in jobs
        ]
        return await asyncio.gather(*tasks)

    async def _generate_one_safe(
        self, text: str, output: Path, voice: str | None, speed: str
    ) -> int:
        """Generate one chapter inside the shared event loop.

        Awaits the async core directly rather than going through the synchronous
        ``generate()``, which would nest ``asyncio.run()`` inside the running loop.
        """
        voice = voice or DEFAULT_VOICE
        rate = normalize_edge_speed(speed)
        try:
            await self._generate_async(text, str(output), voice, rate)
            return get_audio_duration_ms(output) if output.exists() else 0
        except Exception as exc:
            logger.error("Edge-TTS error for %s: %s", output, exc)
            return 0
