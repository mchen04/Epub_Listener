"""Section-4 accuracy gate: transcript timestamps proved against real audio.

Ground truth comes from a calibration chapter of isolated words separated by
silence: speech onsets are detected acoustically (energy rise after a silence
gap) in the FINAL assembled MP3, then compared with the transcript's claimed
word starts anchored at the MP3's own chapter markers.

Run explicitly (deselected by default):
    venv/bin/python -m pytest tests/integration/test_accuracy_gate.py -m live -s
"""

from __future__ import annotations

import gzip
import json
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from epub_listener.application.commands import BuildAudiobookCommand
from epub_listener.application.orchestrator import BuildAudiobookUseCase
from epub_listener.domain.models import Chapter
from epub_listener.domain.transcript import (
    GEOB_DESCRIPTION,
    BookTranscript,
    parse_book_transcript,
)
from epub_listener.infrastructure.media.ffmpeg_assembler import FFmpegMediaAssembler
from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder
from epub_listener.infrastructure.media.transcript_embedder import Id3TranscriptEmbedder
from epub_listener.infrastructure.persistence.json_tracker import JsonProgressTracker
from epub_listener.infrastructure.tts.batch import SequentialTTSBatchGenerator
from epub_listener.infrastructure.tts.edge_tts import EdgeTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import KokoroTTSProvider

CALIBRATION_WORDS = [
    "Alpha",
    "Bravo",
    "Charlie",
    "Delta",
    "Echo",
    "Foxtrot",
    "Golf",
    "Hotel",
    "India",
    "Juliet",
    "Kilo",
    "Lima",
    "Mike",
    "November",
    "Oscar",
    "Papa",
    "Quebec",
    "Romeo",
    "Sierra",
    "Tango",
    "Uniform",
    "Victor",
    "Whiskey",
    "Yankee",
]

PROSE = (
    "The lighthouse keeper climbed the narrow stairs every evening. "
    "Salt wind pressed against the glass while gulls wheeled below. "
    "He trimmed the wick and watched the beam sweep the dark water."
)
DIALOGUE = (
    '"Stay close to the wall," said the guide. '
    '"The tide turns quickly here." '
    'Nobody argued with her. "Good," she said. "Then we move at dawn."'
)
NUMBERS = (
    "Route 66 opened in Nov. 1926 and ran 2448 miles. "
    "Dr. Avery measured 45% humidity at 6 a.m. that day. "
    "The No. 3 engine burned 1200 gallons."
)


@dataclass
class GateResult:
    engine: str
    word_errors_ms: list[float]
    reencode_shift_ms: dict[int, float]
    sentence_coverage: dict[int, tuple[int, int]]


def _chapters() -> list[Chapter]:
    calibration = "\n".join(f"{word}." for word in CALIBRATION_WORDS)
    return [
        Chapter("0000", "Calibration", calibration),
        Chapter("0001", "Prose", PROSE),
        Chapter("0002", "Dialogue", DIALOGUE),
        Chapter("0003", "Numbers", NUMBERS),
    ]


class _StaticParser:
    def __init__(self, chapters: list[Chapter]) -> None:
        self._chapters = chapters

    def parse(self, path: Path) -> list[Chapter]:
        return self._chapters


def _build_book(tmp_path: Path, engine: str) -> Path:
    provider = KokoroTTSProvider() if engine == "kokoro" else EdgeTTSProvider()
    work_dir = tmp_path / f"work-{engine}"
    epub = tmp_path / "gate.epub"
    epub.touch()
    use_case = BuildAudiobookUseCase(
        parser=_StaticParser(_chapters()),
        tts=SequentialTTSBatchGenerator(provider),
        assembler=FFmpegMediaAssembler(),
        metadata_builder=FFmpegMetadataBuilder(),
        tracker=JsonProgressTracker(work_dir),
        transcript_embedder=Id3TranscriptEmbedder(),
    )
    command = BuildAudiobookCommand(
        input_epub=epub,
        output_path=tmp_path / f"gate-{engine}.mp3",
        author="Gate",
        voice="af_heart" if engine == "kokoro" else "en-US-AriaNeural",
        speed="+0%",
        temp_dir=work_dir,
        title=f"Accuracy Gate ({engine})",
        tts_backend=engine,
    )
    return use_case.execute(command)


def _read_transcript(mp3: Path) -> BookTranscript:
    from mutagen.id3 import ID3

    frames = [f for f in ID3(mp3).getall("GEOB") if f.desc == GEOB_DESCRIPTION]
    assert len(frames) == 1, "final MP3 must carry exactly one transcript frame"
    return parse_book_transcript(json.loads(gzip.decompress(frames[0].data)))


def _chapter_marks_ms(mp3: Path) -> list[tuple[float, float]]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe is required for the accuracy gate")
    probe = json.loads(
        subprocess.run(  # noqa: S603 - resolved trusted media tool
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_chapters", str(mp3)],
            capture_output=True,
            check=True,
        ).stdout
    )
    # CHAP frame physical order is not guaranteed after the tag rewrite;
    # consumers (and this harness) order chapter markers by start time.
    return sorted(
        (float(ch["start_time"]) * 1000, float(ch["end_time"]) * 1000) for ch in probe["chapters"]
    )


def _decode(mp3: Path, tmp_path: Path, label: str) -> tuple[np.ndarray, int]:
    import soundfile as sf

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for the accuracy gate")
    wav = tmp_path / f"{label}.wav"
    subprocess.run(  # noqa: S603 - resolved trusted media tool
        [ffmpeg, "-y", "-v", "quiet", "-i", str(mp3), str(wav)],
        check=True,
    )
    samples, rate = sf.read(wav, dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return samples, rate


def _envelope(samples: np.ndarray, rate: int, hop_ms: float = 1.0) -> np.ndarray:
    hop = max(1, int(rate * hop_ms / 1000))
    length = (len(samples) // hop) * hop
    if length == 0:
        return np.zeros(0, dtype=np.float64)
    frames = samples[:length].reshape(-1, hop)
    return np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))


def _detect_onsets_ms(samples: np.ndarray, rate: int) -> list[float]:
    """Speech onsets: energy rising through a threshold after >=120ms of quiet."""
    envelope = _envelope(samples, rate)
    if envelope.size == 0:
        return []
    threshold = np.percentile(envelope, 95) * 0.12
    quiet_ms = 120
    onsets: list[float] = []
    below = quiet_ms  # treat stream start as silence
    for index, value in enumerate(envelope):
        if value >= threshold:
            if below >= quiet_ms:
                onsets.append(float(index))
            below = 0
        else:
            below += 1
    return onsets


def _cross_shift_ms(reference: np.ndarray, target: np.ndarray, rate: int) -> float:
    """Lag (ms) of target vs reference via envelope cross-correlation."""
    env_a = _envelope(reference, rate)
    env_b = _envelope(target, rate)
    span = min(len(env_a), len(env_b), 8000)
    env_a = env_a[:span] - env_a[:span].mean()
    env_b = env_b[:span] - env_b[:span].mean()
    max_lag = 200
    best_lag, best_score = 0, -np.inf
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = env_a[: span - lag], env_b[lag:span]
        else:
            a, b = env_a[-lag:span], env_b[: span + lag]
        score = float(np.dot(a, b))
        if score > best_score:
            best_score, best_lag = score, lag
    return float(best_lag)


def _run_gate(tmp_path: Path, engine: str) -> GateResult:
    mp3 = _build_book(tmp_path, engine)
    transcript = _read_transcript(mp3)
    marks = _chapter_marks_ms(mp3)
    assert len(marks) == 4
    samples, rate = _decode(mp3, tmp_path, f"final-{engine}")

    chapters = {chapter.index: chapter for chapter in transcript.chapters}
    calibration = chapters[0]
    claimed = [
        (word.text, word.start_ms) for sentence in calibration.sentences for word in sentence.words
    ]

    start_index = int(marks[0][0] * rate / 1000)
    end_index = int(marks[0][1] * rate / 1000)
    onsets = _detect_onsets_ms(samples[start_index:end_index], rate)
    assert len(onsets) == len(
        CALIBRATION_WORDS
    ), f"expected {len(CALIBRATION_WORDS)} acoustic onsets, found {len(onsets)}"
    if claimed:
        assert len(claimed) == len(CALIBRATION_WORDS)
        errors = [
            abs(claimed_ms - onset) for (_, claimed_ms), onset in zip(claimed, onsets, strict=True)
        ]
    else:
        # Sentence-granularity engine: gate sentence starts instead.
        sentence_starts = [sentence.start_ms for sentence in calibration.sentences]
        assert len(sentence_starts) == len(CALIBRATION_WORDS)
        errors = [abs(start - onset) for start, onset in zip(sentence_starts, onsets, strict=True)]

    shifts: dict[int, float] = {}
    for index, (start_ms, _end_ms) in enumerate(marks):
        source = tmp_path / f"work-{engine}" / f"chap_{index:04d}.mp3"
        source_samples, source_rate = _decode(source, tmp_path, f"src-{engine}-{index}")
        begin = int(start_ms * rate / 1000)
        region = samples[begin : begin + len(source_samples) + rate]
        shifts[index] = _cross_shift_ms(source_samples, region, rate)

    coverage: dict[int, tuple[int, int]] = {}
    from epub_listener.domain.alignment import split_sentence_spans

    for chapter in _chapters():
        index = int(chapter.id)
        expected_spans = len(split_sentence_spans(chapter.text))
        coverage[index] = (len(chapters[index].sentences), expected_spans)

    return GateResult(engine, errors, shifts, coverage)


@pytest.mark.live
@pytest.mark.parametrize("engine", ["kokoro", "edge"])
def test_accuracy_gate(tmp_path: Path, engine: str) -> None:
    result = _run_gate(tmp_path, engine)
    median = statistics.median(result.word_errors_ms)
    p95 = float(np.percentile(result.word_errors_ms, 95))
    print(f"\n[{engine}] word-onset error ms: median={median:.1f} p95={p95:.1f}")
    print(f"[{engine}] re-encode shift ms per chapter: {result.reencode_shift_ms}")
    print(f"[{engine}] sentence coverage (transcript vs source): {result.sentence_coverage}")

    for index, (got, expected) in result.sentence_coverage.items():
        assert got == expected, f"chapter {index}: {got} sentences vs {expected} in source"
    assert median <= 30, f"median onset error {median:.1f}ms exceeds 30ms"
    assert p95 <= 100, f"p95 onset error {p95:.1f}ms exceeds 100ms"
    assert max(abs(v) for v in result.reencode_shift_ms.values()) <= 50
