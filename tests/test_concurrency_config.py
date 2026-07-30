from pathlib import Path

import pytest
from pydantic import ValidationError

from epub_listener.concurrency import ConcurrencyStrategy
from epub_listener.config import Settings
from epub_listener.domain.exceptions import ConfigurationError
from epub_listener.infrastructure.tts.batch import SequentialTTSBatchGenerator
from epub_listener.infrastructure.tts.edge_tts import EdgeAsyncTTSBatchGenerator
from epub_listener.infrastructure.tts.factory import create_tts_batch_generator
from epub_listener.infrastructure.tts.kokoro_tts import KokoroParallelTTSBatchGenerator
from epub_listener.infrastructure.tts.mlx_kokoro_tts import KokoroMLXTTSProvider


@pytest.mark.parametrize("mode", ["auto", "async"])
def test_edge_factory_selects_async_batcher_for_async_modes(
    mode: ConcurrencyStrategy,
) -> None:
    batch = create_tts_batch_generator(
        use_kokoro=False,
        concurrency=mode,
        max_workers=2,
    )

    assert isinstance(batch, EdgeAsyncTTSBatchGenerator)
    assert batch.max_concurrent == 2


def test_edge_factory_selects_sequential_batcher() -> None:
    batch = create_tts_batch_generator(
        use_kokoro=False,
        concurrency="sequential",
        max_workers=2,
    )

    assert isinstance(batch, SequentialTTSBatchGenerator)


def test_edge_factory_rejects_process_parallelism() -> None:
    with pytest.raises(ConfigurationError, match="Edge-TTS does not support"):
        create_tts_batch_generator(
            use_kokoro=False,
            concurrency="parallel",
            max_workers=2,
        )


@pytest.mark.parametrize("mode", ["auto", "parallel"])
def test_kokoro_factory_selects_parallel_batcher_for_parallel_modes(
    mode: ConcurrencyStrategy,
) -> None:
    batch = create_tts_batch_generator(
        use_kokoro=True,
        concurrency=mode,
        max_workers=2,
    )

    assert isinstance(batch, KokoroParallelTTSBatchGenerator)
    assert batch.max_workers == 2


def test_kokoro_factory_selects_sequential_batcher() -> None:
    batch = create_tts_batch_generator(
        use_kokoro=True,
        concurrency="sequential",
        max_workers=2,
    )

    assert isinstance(batch, SequentialTTSBatchGenerator)


def test_kokoro_factory_selects_tuned_two_worker_hybrid() -> None:
    batch = create_tts_batch_generator(
        use_kokoro=True,
        concurrency="parallel",
        max_workers=8,
        kokoro_hybrid_mps=True,
    )

    assert isinstance(batch, KokoroParallelTTSBatchGenerator)
    assert batch.hybrid_mps
    assert batch.max_workers == 2


def test_kokoro_hybrid_rejects_sequential_mode() -> None:
    with pytest.raises(ConfigurationError, match="requires --concurrency parallel or auto"):
        create_tts_batch_generator(
            use_kokoro=True,
            concurrency="sequential",
            max_workers=2,
            kokoro_hybrid_mps=True,
        )


def test_kokoro_hybrid_requires_kokoro_backend() -> None:
    with pytest.raises(ConfigurationError, match="requires --use-kokoro"):
        create_tts_batch_generator(
            use_kokoro=False,
            concurrency="async",
            max_workers=2,
            kokoro_hybrid_mps=True,
        )


def test_kokoro_mlx_selects_sequential_provider() -> None:
    batch = create_tts_batch_generator(
        use_kokoro=True,
        concurrency="sequential",
        max_workers=8,
        kokoro_mlx=True,
    )

    assert isinstance(batch, SequentialTTSBatchGenerator)
    assert isinstance(batch._provider, KokoroMLXTTSProvider)


def test_kokoro_mlx_forwards_preset_to_provider() -> None:
    batch = create_tts_batch_generator(
        use_kokoro=True,
        concurrency="sequential",
        max_workers=8,
        kokoro_mlx=True,
        kokoro_preset="student-fast",
    )

    assert batch._provider._preset == "student-fast"


def test_kokoro_preset_requires_mlx_backend() -> None:
    with pytest.raises(ConfigurationError, match="--kokoro-preset requires --kokoro-mlx"):
        create_tts_batch_generator(
            use_kokoro=True,
            concurrency="sequential",
            max_workers=8,
            kokoro_preset="exact",
        )


def test_kokoro_mlx_rejects_parallel_mode() -> None:
    with pytest.raises(ConfigurationError, match="requires --concurrency sequential or auto"):
        create_tts_batch_generator(
            use_kokoro=True,
            concurrency="parallel",
            max_workers=2,
            kokoro_mlx=True,
        )


def test_kokoro_mlx_and_hybrid_are_mutually_exclusive() -> None:
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        create_tts_batch_generator(
            use_kokoro=True,
            concurrency="auto",
            max_workers=2,
            kokoro_mlx=True,
            kokoro_hybrid_mps=True,
        )


def test_kokoro_factory_rejects_async_io_mode() -> None:
    with pytest.raises(ConfigurationError, match="Kokoro does not support"):
        create_tts_batch_generator(
            use_kokoro=True,
            concurrency="async",
            max_workers=2,
        )


def _mlx_settings(tmp_path: Path, **overrides: object) -> Settings:
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"stub")
    return Settings(input_epub=epub, use_kokoro=True, kokoro_mlx=True, **overrides)


def test_student_preset_rejects_non_default_speed(tmp_path: Path) -> None:
    """The engine raises on speed != 1.0, but only once a chapter is synthesized."""
    with pytest.raises(ValidationError, match=r"supports only --speed \+0%"):
        _mlx_settings(tmp_path, kokoro_preset="student-fast", speed="+20%")


def test_student_preset_rejects_other_voices(tmp_path: Path) -> None:
    """The engine silently ignores the requested voice, so reject it up front."""
    with pytest.raises(ValidationError, match="single-voice"):
        _mlx_settings(tmp_path, kokoro_preset="student-fast", kokoro_voice="bm_george")


def test_student_preset_accepts_its_own_voice_and_default_speed(tmp_path: Path) -> None:
    settings = _mlx_settings(tmp_path, kokoro_preset="student-fast", kokoro_voice="af_heart")

    assert settings.kokoro_preset == "student-fast"


def test_full_presets_keep_speed_and_voice_control(tmp_path: Path) -> None:
    settings = _mlx_settings(
        tmp_path, kokoro_preset="exact", speed="+20%", kokoro_voice="bm_george"
    )

    assert settings.speed == "+20%"
