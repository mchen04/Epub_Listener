"""Kokoro-82M local TTS provider."""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from epub_listener.application.ports import ConcurrencyStrategy, TTSProvider
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.infrastructure.tts.base import edge_speed_to_multiplier
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "af_heart"
DEFAULT_LANG = "a"


class KokoroTTSProvider(TTSProvider):
    """Generates audio using local Kokoro-82M inference."""

    def __init__(self) -> None:
        self._pipelines: dict[str, Any] = {}

    def supports_concurrency(self) -> ConcurrencyStrategy:
        return "parallel"

    def _get_pipeline(self, lang_code: str) -> Any:
        """Lazy-load and cache Kokoro pipelines by language code."""
        if lang_code not in self._pipelines:
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise TTSGenerationError(
                    "Kokoro is not installed. Run: pip install kokoro>=0.9.4 soundfile"
                ) from exc
            self._pipelines[lang_code] = KPipeline(lang_code=lang_code)
        return self._pipelines[lang_code]

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate audio and return duration in ms."""
        voice = voice or DEFAULT_VOICE
        lang = self._infer_lang(voice)
        try:
            import soundfile as sf
        except ImportError as exc:
            raise TTSGenerationError("soundfile not installed") from exc

        try:
            pipeline = self._get_pipeline(lang)
            speed_float = edge_speed_to_multiplier(speed)
            generator = pipeline(text, voice=voice, speed=speed_float)

            segments: list[np.ndarray] = []
            sample_rate = 24000
            for _, _, audio in generator:
                segments.append(audio)

            if not segments:
                logger.warning("Kokoro produced no audio segments for %s", output)
                return 0

            full_audio = np.concatenate(segments)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_wav_path = Path(tmp_wav.name)

            try:
                sf.write(tmp_wav_path, full_audio, sample_rate)
                run_ffmpeg("-i", tmp_wav_path, "-codec:a", "libmp3lame", "-q:a", "2", output)
            finally:
                if tmp_wav_path.exists():
                    os.remove(tmp_wav_path)

            if output.exists():
                return get_audio_duration_ms(output)
        except TTSGenerationError:
            raise
        except Exception as exc:
            logger.error("Kokoro error for %s: %s", output, exc)
            raise TTSGenerationError(f"Kokoro error: {exc}") from exc
        return 0

    async def generate_many(
        self,
        jobs: list[tuple[str, Path, str | None, str]],
    ) -> list[int]:
        raise NotImplementedError("Kokoro does not support async batch generation.")

    def _infer_lang(self, voice: str) -> str:
        """Infer Kokoro language code from voice prefix.

        Defaults to American English ('a').
        """
        if voice.startswith("b"):
            return "b"
        return DEFAULT_LANG
