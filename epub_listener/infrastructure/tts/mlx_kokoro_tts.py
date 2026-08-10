"""Apple MLX implementation of the Kokoro-82M TTS provider.

Uses the FastKokoro engine (github: local `fastkoko` package) when installed:
  - fixes two upstream mlx-audio numerical bugs (iSTFT COLA normalization,
    AdainResBlk1d upsample misalignment) so output matches the PyTorch
    reference model — the old +2.7 dB OUTPUT_GAIN hack is gone;
  - folds weight-norm, fuses AdaIN, caches the voice pack;
  - optional weight quantization via EPUB_KOKORO_QUANT_BITS (unset = bf16);
  - reports word-level timestamps (previously sentence-level only).

Falls back to the plain mlx-audio path (with the legacy gain hack) when
fastkoko is not installed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

from epub_listener.application.ports import TTSJob
from epub_listener.domain.alignment import RawWordCue
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.domain.speed import speed_to_multiplier
from epub_listener.infrastructure.tts.base import infer_kokoro_lang_for_voice
from epub_listener.infrastructure.tts.finalize import commit_generated_mp3
from epub_listener.infrastructure.tts.ports import TTSProvider
from epub_listener.infrastructure.tts.transcript_capture import (
    KokoroTokenWalker,
    capture_chapter_transcript,
)
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
DEFAULT_VOICE = "af_heart"
MODEL_REPO = "mlx-community/Kokoro-82M-bf16"
# Legacy fallback only (see module docstring): the *unfixed* converted decoder
# is 2.7 dB quieter than the PyTorch reference.
OUTPUT_GAIN_DB = 2.7
OUTPUT_GAIN = 10 ** (OUTPUT_GAIN_DB / 20)

_ENGINES: dict[str, Any] = {}
_MODEL: Any | None = None


def _quiet_phonemizer() -> None:
    phonemizer_logger = logging.getLogger("phonemizer")
    phonemizer_logger.setLevel(logging.ERROR)
    phonemizer_logger.disabled = True


DEFAULT_PRESET = "ship-q8"


def _get_engine(preset: str | None = None) -> Any | None:
    """FastKokoro engine for `preset`, or None if fastkoko is not installed.

    Presets: "ship-q8" (default; 114 MB artifact, passes every
    floor-calibrated quality gate), "ship-q4" (87 MB, slight worst-case
    timbre shift), "exact" (fp32, bit-clean vs the PyTorch reference),
    "student-fast" (distilled ~10M params, far faster; af_heart only,
    speed 1.0 only, no per-token timestamps).

    Falls back to EPUB_KOKORO_PRESET when no preset is passed. Engines are
    cached per preset so a mixed-preset process loads each one once.
    """
    _quiet_phonemizer()
    env_preset = os.environ.get("EPUB_KOKORO_PRESET", "").strip()
    requested = preset or env_preset or None
    try:
        from fastkoko import from_preset
    except ImportError:
        if requested is not None:
            raise TTSGenerationError(
                f"FastKokoro preset '{requested}' was requested, but fastkoko is not installed"
            ) from None
        return None
    resolved = requested or DEFAULT_PRESET
    if resolved not in _ENGINES:
        try:
            _ENGINES[resolved] = from_preset(resolved)
        except Exception as exc:
            raise TTSGenerationError(
                f"Could not initialize FastKokoro ({resolved}): {exc}"
            ) from exc
    return _ENGINES[resolved]


def _get_model() -> Any:
    """Legacy mlx-audio model path (only used when fastkoko is unavailable)."""
    global _MODEL
    if _MODEL is None:
        _quiet_phonemizer()
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

    def __init__(self, preset: str | None = None) -> None:
        self._preset = preset

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        return self.run_job(TTSJob("_single", text, output, voice, speed))

    def run_job(self, job: TTSJob) -> int:
        engine = _get_engine(self._preset)
        if engine is not None:
            return self._run_fast(job, engine)
        return self._run_legacy(job)

    # ---------- FastKokoro path ----------

    def _run_fast(self, job: TTSJob, engine: Any) -> int:
        text, output, voice, speed = job.text, job.output, job.voice, job.speed
        voice = voice or DEFAULT_VOICE
        tmp_wav = output.with_name(f".{output.stem}.tmp.wav")
        tmp_mp3 = output.with_name(f".{output.stem}.tmp{output.suffix}")
        try:
            import soundfile as sf
        except ImportError as exc:
            raise TTSGenerationError("soundfile is not installed") from exc

        try:
            tmp_wav.unlink(missing_ok=True)
            tmp_mp3.unlink(missing_ok=True)
            speed_float = speed_to_multiplier(speed)
            capture = job.transcript_path is not None
            walker = KokoroTokenWalker(text) if capture else None
            word_cues: list[RawWordCue] = []
            chunk_spans: list[tuple[str, int, int]] = []
            total_samples = 0
            with sf.SoundFile(
                tmp_wav, mode="w", samplerate=SAMPLE_RATE, channels=1, format="WAV"
            ) as wav_file:
                for result in engine.synth(text, voice=voice, speed=speed_float):
                    samples = result.audio
                    if samples.size == 0:
                        continue
                    chunk_start_ms = round(total_samples * 1000 / SAMPLE_RATE)
                    wav_file.write(samples)
                    total_samples += int(samples.size)
                    if capture and walker is not None:
                        word_cues.extend(walker.cues_for_chunk(result.tokens, chunk_start_ms))
                        chunk_spans.append(
                            (
                                result.graphemes,
                                chunk_start_ms,
                                round(total_samples * 1000 / SAMPLE_RATE),
                            )
                        )

            if total_samples <= 0:
                raise TTSGenerationError(f"MLX Kokoro produced no audio for {output}")
            if capture and job.transcript_path is not None:
                capture_chapter_transcript(
                    job.transcript_path,
                    job.chapter_id,
                    "kokoro-mlx",
                    text,
                    word_cues,
                    chunk_spans,
                )

            run_ffmpeg("-i", tmp_wav, "-codec:a", "libmp3lame", "-q:a", "2", tmp_mp3)
            return commit_generated_mp3(tmp_mp3, output)
        except TTSGenerationError:
            raise
        except Exception as exc:
            logger.error("MLX Kokoro error for %s: %s", output, exc)
            raise TTSGenerationError(f"MLX Kokoro error: {exc}") from exc
        finally:
            tmp_wav.unlink(missing_ok=True)
            tmp_mp3.unlink(missing_ok=True)

    # ---------- legacy mlx-audio path ----------

    def _run_legacy(self, job: TTSJob) -> int:
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
            speed_float = speed_to_multiplier(speed)
            total_samples = 0
            clipped_samples = 0
            chunk_spans: list[tuple[str, int, int]] = []
            with sf.SoundFile(
                tmp_wav, mode="w", samplerate=SAMPLE_RATE, channels=1, format="WAV"
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

            run_ffmpeg("-i", tmp_wav, "-codec:a", "libmp3lame", "-q:a", "2", tmp_mp3)
            return commit_generated_mp3(tmp_mp3, output)
        except TTSGenerationError:
            raise
        except Exception as exc:
            logger.error("MLX Kokoro error for %s: %s", output, exc)
            raise TTSGenerationError(f"MLX Kokoro error: {exc}") from exc
        finally:
            tmp_wav.unlink(missing_ok=True)
            tmp_mp3.unlink(missing_ok=True)
