"""Reusable base for local TTS engines that return waveform samples."""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from epub_listener.application.ports import TTSJob
from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.domain.speed import (
    MAX_SPEED_MULTIPLIER,
    MIN_SPEED_MULTIPLIER,
    speed_to_multiplier,
)
from epub_listener.infrastructure.tts.finalize import commit_generated_mp3
from epub_listener.infrastructure.tts.transcript_capture import capture_chapter_transcript
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg

logger = logging.getLogger(__name__)

MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 384_000


@dataclass(frozen=True)
class AudioChunk:
    """One synthesized waveform and its sample rate."""

    samples: Any
    sample_rate: int


def split_for_tts(text: str, max_chars: int) -> list[str]:
    """Split long prose at sentence/word boundaries without dropping text.

    TTS model token limits vary widely. A conservative character boundary is
    model-agnostic, and users can tune or disable it per model.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    cursor = 0
    length = len(cleaned)
    while cursor < length:
        hard_end = min(length, cursor + max_chars)
        if hard_end == length:
            split_at = length
        else:
            lower_bound = cursor + max(1, max_chars // 2)
            window = cleaned[lower_bound:hard_end]
            sentence_offsets = [
                index + 1 for index, character in enumerate(window) if character in ".!?\u2026\n"
            ]
            if sentence_offsets:
                split_at = lower_bound + sentence_offsets[-1]
            else:
                whitespace = cleaned.rfind(" ", lower_bound, hard_end)
                split_at = whitespace if whitespace > cursor else hard_end

        piece = cleaned[cursor:split_at].strip()
        if piece:
            chunks.append(piece)
        cursor = split_at
        while cursor < length and cleaned[cursor].isspace():
            cursor += 1
    return chunks


def atempo_filter(multiplier: float) -> str | None:
    """Build a portable ffmpeg ``atempo`` chain for an arbitrary positive rate."""
    if (
        not math.isfinite(multiplier)
        or multiplier < MIN_SPEED_MULTIPLIER
        or multiplier > MAX_SPEED_MULTIPLIER
    ):
        raise TTSGenerationError("Playback speed must be between 0.1x and 16x")
    if math.isclose(multiplier, 1.0, rel_tol=0.0, abs_tol=1e-9):
        return None

    stages: list[float] = []
    remaining = multiplier
    # Older ffmpeg releases accept only 0.5..2.0 per atempo stage.
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    stages.append(remaining)
    return ",".join(f"atempo={stage:.8g}" for stage in stages)


def normalize_audio_chunk(chunk: AudioChunk, *, engine: str) -> tuple[np.ndarray, int]:
    """Validate an engine waveform and return soundfile-compatible float32 samples."""
    try:
        sample_rate = int(chunk.sample_rate)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TTSGenerationError(f"{engine} returned an invalid sample rate") from exc
    if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise TTSGenerationError(
            f"{engine} returned unsupported sample rate {sample_rate}; "
            f"expected {MIN_SAMPLE_RATE}..{MAX_SAMPLE_RATE} Hz"
        )

    try:
        samples = np.asarray(chunk.samples, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise TTSGenerationError(f"{engine} returned non-numeric audio") from exc
    samples = np.squeeze(samples)
    if samples.ndim == 0 or samples.size == 0:
        raise TTSGenerationError(f"{engine} returned no audio samples")
    if samples.ndim > 2:
        raise TTSGenerationError(
            f"{engine} returned audio with {samples.ndim} dimensions; expected mono or stereo"
        )
    if samples.ndim == 2:
        # Transformers documents channel-first audio; soundfile needs frame-first.
        if samples.shape[0] <= 8 and samples.shape[1] > samples.shape[0]:
            samples = samples.T
        elif samples.shape[1] > 8:
            raise TTSGenerationError(
                f"{engine} returned ambiguous audio shape {tuple(samples.shape)}"
            )
    if not np.isfinite(samples).all():
        raise TTSGenerationError(f"{engine} returned NaN or infinite audio samples")

    peak = float(np.max(np.abs(samples)))
    if peak > 1.0:
        logger.warning("%s audio peak %.3f exceeded 1.0; clipping", engine, peak)
        samples = np.clip(samples, -1.0, 1.0)
    return np.ascontiguousarray(samples, dtype=np.float32), sample_rate


class WaveformTTSProvider(ABC):
    """Shared chunking, WAV streaming, speed handling, and atomic MP3 commit."""

    def __init__(
        self,
        *,
        engine_name: str,
        chunk_chars: int,
        chunk_pause_ms: int,
    ) -> None:
        self.engine_name = engine_name
        self.chunk_chars = chunk_chars
        self.chunk_pause_ms = chunk_pause_ms

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        return self.run_job(TTSJob("_single", text, output, voice, speed))

    def run_job(self, job: TTSJob) -> int:
        chunks = split_for_tts(job.text, self.chunk_chars)
        if not chunks:
            raise TTSGenerationError(f"{self.engine_name} received empty text")

        tmp_wav = job.output.with_name(f".{job.output.stem}.waveform.tmp.wav")
        tmp_mp3 = job.output.with_name(f".{job.output.stem}.waveform.tmp.mp3")
        chunk_spans: list[tuple[str, int, int]] = []
        try:
            import soundfile as sf
        except ImportError as exc:
            raise TTSGenerationError("soundfile is not installed") from exc

        try:
            tmp_wav.unlink(missing_ok=True)
            tmp_mp3.unlink(missing_ok=True)
            first_audio, output_rate = normalize_audio_chunk(
                self.synthesize_chunk(
                    chunks[0],
                    job.voice,
                    work_dir=job.output.parent,
                    chunk_index=0,
                ),
                engine=self.engine_name,
            )
            output_channels = 1 if first_audio.ndim == 1 else int(first_audio.shape[1])
            total_frames = 0

            with sf.SoundFile(
                tmp_wav,
                mode="w",
                samplerate=output_rate,
                channels=output_channels,
                format="WAV",
            ) as wav:
                for index, text_chunk in enumerate(chunks):
                    if index == 0:
                        normalized, sample_rate = first_audio, output_rate
                    else:
                        normalized, sample_rate = normalize_audio_chunk(
                            self.synthesize_chunk(
                                text_chunk,
                                job.voice,
                                work_dir=job.output.parent,
                                chunk_index=index,
                            ),
                            engine=self.engine_name,
                        )
                    channels = 1 if normalized.ndim == 1 else int(normalized.shape[1])
                    if sample_rate != output_rate or channels != output_channels:
                        raise TTSGenerationError(
                            f"{self.engine_name} changed audio format between chunks "
                            f"({output_rate} Hz/{output_channels} ch to "
                            f"{sample_rate} Hz/{channels} ch)"
                        )

                    start_ms = round(total_frames * 1000 / sample_rate)
                    wav.write(normalized)
                    frame_count = int(normalized.shape[0])
                    total_frames += frame_count
                    end_ms = round(total_frames * 1000 / sample_rate)
                    chunk_spans.append((text_chunk, start_ms, end_ms))

                    if index + 1 < len(chunks) and self.chunk_pause_ms:
                        pause_frames = round(sample_rate * self.chunk_pause_ms / 1000)
                        pause_shape = (
                            pause_frames
                            if output_channels == 1
                            else (pause_frames, output_channels)
                        )
                        wav.write(np.zeros(pause_shape, dtype=np.float32))
                        total_frames += pause_frames

            if total_frames <= 0:
                raise TTSGenerationError(f"{self.engine_name} produced no audio for {job.output}")

            multiplier = speed_to_multiplier(job.speed)
            ffmpeg_args: list[str | Path] = ["-i", tmp_wav]
            tempo = atempo_filter(multiplier)
            if tempo:
                ffmpeg_args.extend(("-filter:a", tempo))
            ffmpeg_args.extend(("-codec:a", "libmp3lame", "-q:a", "2", tmp_mp3))
            run_ffmpeg(*ffmpeg_args)

            if job.transcript_path is not None:
                scaled_spans = [
                    (text, round(start / multiplier), round(end / multiplier))
                    for text, start, end in chunk_spans
                ]
                capture_chapter_transcript(
                    job.transcript_path,
                    job.chapter_id,
                    self.engine_name,
                    job.text,
                    [],
                    scaled_spans,
                )
            return commit_generated_mp3(tmp_mp3, job.output)
        except TTSGenerationError:
            raise
        except Exception as exc:
            logger.error("%s error for %s: %s", self.engine_name, job.output, exc)
            raise TTSGenerationError(f"{self.engine_name} error: {exc}") from exc
        finally:
            tmp_wav.unlink(missing_ok=True)
            tmp_mp3.unlink(missing_ok=True)

    @abstractmethod
    def synthesize_chunk(
        self,
        text: str,
        voice: str | None,
        *,
        work_dir: Path,
        chunk_index: int,
    ) -> AudioChunk:
        """Synthesize one bounded text chunk."""
