import pytest

from epub_listener.concurrency import ConcurrencyStrategy
from epub_listener.domain.exceptions import ConfigurationError
from epub_listener.infrastructure.tts.batch import SequentialTTSBatchGenerator
from epub_listener.infrastructure.tts.edge_tts import EdgeAsyncTTSBatchGenerator
from epub_listener.infrastructure.tts.factory import create_tts_batch_generator
from epub_listener.infrastructure.tts.kokoro_tts import KokoroParallelTTSBatchGenerator


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


def test_kokoro_factory_rejects_async_io_mode() -> None:
    with pytest.raises(ConfigurationError, match="Kokoro does not support"):
        create_tts_batch_generator(
            use_kokoro=True,
            concurrency="async",
            max_workers=2,
        )
