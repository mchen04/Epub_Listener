"""TTS batch generator factory and backend capability policy."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from epub_listener.application.ports import TTSBatchGenerator
from epub_listener.concurrency import ConcurrencyStrategy
from epub_listener.domain.exceptions import ConfigurationError
from epub_listener.domain.tts import TTSEngine
from epub_listener.infrastructure.tts.batch import SequentialTTSBatchGenerator
from epub_listener.infrastructure.tts.command_tts import CommandTTSProvider
from epub_listener.infrastructure.tts.edge_tts import EdgeAsyncTTSBatchGenerator, EdgeTTSProvider
from epub_listener.infrastructure.tts.huggingface_tts import HuggingFaceTTSProvider
from epub_listener.infrastructure.tts.kokoro_tts import (
    KokoroParallelTTSBatchGenerator,
    KokoroTTSProvider,
)
from epub_listener.infrastructure.tts.mlx_kokoro_tts import KokoroMLXTTSProvider


def create_tts_batch_generator(
    *,
    concurrency: ConcurrencyStrategy,
    max_workers: int,
    engine: TTSEngine | None = None,
    use_kokoro: bool | None = None,
    kokoro_hybrid_mps: bool = False,
    kokoro_mlx: bool = False,
    kokoro_preset: str | None = None,
    model: str | None = None,
    revision: str | None = None,
    device: str = "auto",
    dtype: str = "auto",
    trust_remote_code: bool = False,
    local_files_only: bool = False,
    model_options: Mapping[str, Mapping[str, Any]] | None = None,
    speaker_embedding: Path | None = None,
    chunk_chars: int = 500,
    chunk_pause_ms: int = 80,
    model_command: str | None = None,
    command_output_format: str = "wav",
    model_timeout: int = 1800,
) -> TTSBatchGenerator:
    selected = _select_engine(engine, use_kokoro, kokoro_mlx)
    if kokoro_hybrid_mps and kokoro_mlx:
        raise ConfigurationError("--kokoro-hybrid-mps and --kokoro-mlx are mutually exclusive")
    if engine is None and kokoro_mlx and not use_kokoro:
        raise ConfigurationError("--kokoro-mlx requires --use-kokoro")
    if kokoro_preset and selected != "kokoro-mlx":
        raise ConfigurationError("--kokoro-preset requires --kokoro-mlx")
    if kokoro_hybrid_mps and selected != "kokoro":
        raise ConfigurationError("--kokoro-hybrid-mps requires --use-kokoro")
    if selected in {"kokoro", "kokoro-mlx"}:
        if selected == "kokoro-mlx":
            if concurrency not in ("auto", "sequential"):
                raise ConfigurationError("--kokoro-mlx requires --concurrency sequential or auto")
            return SequentialTTSBatchGenerator(KokoroMLXTTSProvider(preset=kokoro_preset))
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

    if selected == "huggingface":
        if model is None:
            raise ConfigurationError("--engine huggingface requires --model")
        _require_sequential_local(concurrency, "Hugging Face")
        return SequentialTTSBatchGenerator(
            HuggingFaceTTSProvider(
                model=model,
                revision=revision,
                device=device,
                dtype=dtype,
                trust_remote_code=trust_remote_code,
                local_files_only=local_files_only,
                model_options=model_options,
                speaker_embedding=speaker_embedding,
                chunk_chars=chunk_chars,
                chunk_pause_ms=chunk_pause_ms,
            )
        )

    if selected == "command":
        if model_command is None:
            raise ConfigurationError("--engine command requires --model-command")
        _require_sequential_local(concurrency, "Local command")
        return SequentialTTSBatchGenerator(
            CommandTTSProvider(
                command=model_command,
                output_format=command_output_format,
                timeout_seconds=model_timeout,
                chunk_chars=chunk_chars,
                chunk_pause_ms=chunk_pause_ms,
            )
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


def _select_engine(
    engine: TTSEngine | None,
    use_kokoro: bool | None,
    kokoro_mlx: bool,
) -> TTSEngine:
    if engine is not None:
        legacy = "kokoro-mlx" if kokoro_mlx else "kokoro"
        if use_kokoro and engine != legacy:
            raise ConfigurationError(
                f"--use-kokoro conflicts with --engine {engine}; use one engine selector"
            )
        return engine
    if use_kokoro:
        return "kokoro-mlx" if kokoro_mlx else "kokoro"
    return "edge"


def _require_sequential_local(concurrency: ConcurrencyStrategy, backend: str) -> None:
    if concurrency not in ("auto", "sequential"):
        raise ConfigurationError(
            f"{backend} models share process memory and require --concurrency sequential or auto"
        )


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
