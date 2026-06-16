"""TTS batch generator factory and backend capability policy."""

from epub_listener.application.ports import TTSBatchGenerator
from epub_listener.concurrency import ConcurrencyStrategy
from epub_listener.domain.exceptions import ConfigurationError
from epub_listener.infrastructure.tts.batch import SequentialTTSBatchGenerator
from epub_listener.infrastructure.tts.edge_tts import EdgeAsyncTTSBatchGenerator, EdgeTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import (
    KokoroParallelTTSBatchGenerator,
    KokoroTTSProvider,
)


def create_tts_batch_generator(
    *,
    use_kokoro: bool,
    concurrency: ConcurrencyStrategy,
    max_workers: int,
) -> TTSBatchGenerator:
    if use_kokoro:
        mode = _resolve_backend_concurrency(
            concurrency,
            backend="Kokoro",
            supported=("sequential", "parallel"),
            auto_mode="parallel",
        )
        if mode == "sequential":
            return SequentialTTSBatchGenerator(KokoroTTSProvider())
        return KokoroParallelTTSBatchGenerator(max_workers=max_workers)

    mode = _resolve_backend_concurrency(
        concurrency,
        backend="Edge-TTS",
        supported=("sequential", "async"),
        auto_mode="async",
    )
    provider = EdgeTTSProvider()
    if mode == "sequential":
        return SequentialTTSBatchGenerator(provider)
    return EdgeAsyncTTSBatchGenerator(provider, max_concurrent=max_workers)


def _resolve_backend_concurrency(
    concurrency: ConcurrencyStrategy,
    *,
    backend: str,
    supported: tuple[ConcurrencyStrategy, ...],
    auto_mode: ConcurrencyStrategy,
) -> ConcurrencyStrategy:
    if concurrency == "auto":
        return auto_mode
    if concurrency in supported:
        return concurrency
    allowed = ", ".join(("auto", *supported))
    raise ConfigurationError(
        f"{backend} does not support --concurrency {concurrency}; use one of: {allowed}"
    )
