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
from epub_listener.infrastructure.tts.mlx_kokoro_tts import KokoroMLXTTSProvider


def create_tts_batch_generator(
    *,
    use_kokoro: bool,
    concurrency: ConcurrencyStrategy,
    max_workers: int,
    kokoro_hybrid_mps: bool = False,
    kokoro_mlx: bool = False,
) -> TTSBatchGenerator:
    if kokoro_hybrid_mps and kokoro_mlx:
        raise ConfigurationError("--kokoro-hybrid-mps and --kokoro-mlx are mutually exclusive")
    if kokoro_mlx and not use_kokoro:
        raise ConfigurationError("--kokoro-mlx requires --use-kokoro")
    if kokoro_hybrid_mps and not use_kokoro:
        raise ConfigurationError("--kokoro-hybrid-mps requires --use-kokoro")
    if use_kokoro:
        if kokoro_mlx:
            if concurrency not in ("auto", "sequential"):
                raise ConfigurationError(
                    "--kokoro-mlx requires --concurrency sequential or auto"
                )
            return SequentialTTSBatchGenerator(KokoroMLXTTSProvider())
        mode = _resolve_backend_concurrency(
            concurrency,
            backend="Kokoro",
            supported=("sequential", "parallel"),
            auto_mode="parallel",
        )
        if mode == "sequential":
            if kokoro_hybrid_mps:
                raise ConfigurationError(
                    "--kokoro-hybrid-mps requires --concurrency parallel or auto"
                )
            return SequentialTTSBatchGenerator(KokoroTTSProvider())
        return KokoroParallelTTSBatchGenerator(
            max_workers=max_workers,
            hybrid_mps=kokoro_hybrid_mps,
        )

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
