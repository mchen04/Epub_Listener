"""Apple MLX implementation of the Kokoro-82M TTS provider."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from epub_listener.application.ports import TTSJob
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.infrastructure.tts.base import (
    edge_speed_to_multiplier,
    infer_kokoro_lang_for_voice,
)
from epub_listener.infrastructure.tts.finalize import commit_generated_mp3
from epub_listener.infrastructure.tts.ports import TTSProvider
from epub_listener.infrastructure.tts.transcript_capture import capture_chapter_transcript
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
DEFAULT_VOICE = "af_heart"
MODEL_REPO = "mlx-community/Kokoro-82M-bf16"
# The converted BF16 MLX decoder is consistently 2.7 dB quieter than the
# reference PyTorch Kokoro decoder. Apply a fixed linear gain so cached and
# newly rendered af_heart chapters share the same approximately -25.6 dB mean.
OUTPUT_GAIN_DB = 2.7
OUTPUT_GAIN = 10 ** (OUTPUT_GAIN_DB / 20)

_MODEL: Any | None = None


def _get_model() -> Any:
    global _MODEL
    if _MODEL is None:
        # Misaki's fallback phonemizer can report token bookkeeping mismatches
        # even when the resulting phoneme/audio sequence is complete. The
        # reference and MLX full-chapter outputs have identical duration, so
        # keep this third-party diagnostic from flooding multi-day logs.
        phonemizer_logger = logging.getLogger("phonemizer")
        phonemizer_logger.setLevel(logging.ERROR)
        phonemizer_logger.disabled = True
        try:
            import mlx.core as mx
            from mlx_audio.tts.utils import load
        except ImportError as exc:
            raise TTSGenerationError(
                "MLX Kokoro is not installed. Run: pip install '.[mlx]'"
            ) from exc
        try:
            _MODEL = load(MODEL_REPO)
            mx.eval(_MODEL.parameters())
        except Exception as exc:
            raise TTSGenerationError(f"Could not initialize MLX Kokoro: {exc}") from exc
    return _MODEL


class KokoroMLXTTSProvider(TTSProvider):
    """Generate Kokoro speech through Apple's MLX framework."""

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        return self.run_job(TTSJob("_single", text, output, voice, speed))

    def run_job(self, job: TTSJob) -> int:
        text, output, voice, speed = job.text, job.output, job.voice, job.speed
        voice = voice or DEFAULT_VOICE
        tmp_wav = output.with_name(f".{output.stem}.tmp.wav")
        tmp_mp3 = output.with_name(f".{output.stem}.tmp{output.suffix}")
        try:
            import mlx.core as mx
            import soundfile as sf
        except ImportError as exc:
            raise TTSGenerationError("MLX Kokoro or soundfile is not installed") from exc

        try:
            tmp_wav.unlink(missing_ok=True)
            tmp_mp3.unlink(missing_ok=True)
            model = _get_model()
            speed_float = edge_speed_to_multiplier(speed)
            total_samples = 0
            clipped_samples = 0
            chunk_spans: list[tuple[str, int, int]] = []
            with sf.SoundFile(
                tmp_wav,
                mode="w",
                samplerate=SAMPLE_RATE,
                channels=1,
                format="WAV",
            ) as wav_file:
                for result in model.generate(
                    text,
                    voice=voice,
                    speed=speed_float,
                    lang_code=infer_kokoro_lang_for_voice(voice),
                ):
                    mx.eval(result.audio)
                    samples = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                    if samples.size == 0:
                        continue
                    chunk_start_ms = round(total_samples * 1000 / SAMPLE_RATE)
                    samples = samples * OUTPUT_GAIN
                    clipped_samples += int(np.count_nonzero(np.abs(samples) > 1.0))
                    np.clip(samples, -1.0, 1.0, out=samples)
                    wav_file.write(samples)
                    total_samples += int(samples.size)
                    if job.transcript_path is not None:
                        # The MLX pipeline reports no per-token timestamps, so
                        # capture honest chunk-level spans (sentence fallback).
                        chunk_text = getattr(result, "graphemes", None) or getattr(
                            result, "text", ""
                        )
                        chunk_spans.append(
                            (
                                str(chunk_text),
                                chunk_start_ms,
                                round(total_samples * 1000 / SAMPLE_RATE),
                            )
                        )

            if total_samples <= 0:
                raise TTSGenerationError(f"MLX Kokoro produced no audio for {output}")
            if job.transcript_path is not None:
                capture_chapter_transcript(
                    job.transcript_path,
                    job.chapter_id,
                    "kokoro-mlx",
                    text,
                    [],
                    chunk_spans,
                )
            if clipped_samples:
                logger.warning(
                    "MLX Kokoro clipped %d of %d samples for %s",
                    clipped_samples,
                    total_samples,
                    output,
                )

            run_ffmpeg(
                "-i",
                tmp_wav,
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "2",
                tmp_mp3,
            )
            return commit_generated_mp3(tmp_mp3, output)
        except TTSGenerationError:
            raise
        except Exception as exc:
            logger.error("MLX Kokoro error for %s: %s", output, exc)
            raise TTSGenerationError(f"MLX Kokoro error: {exc}") from exc
        finally:
            tmp_wav.unlink(missing_ok=True)
            tmp_mp3.unlink(missing_ok=True)
