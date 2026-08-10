"""Generic Hugging Face Transformers text-to-speech adapter."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from epub_listener.domain.exceptions import TTSGenerationError
from epub_listener.infrastructure.tts.waveform import AudioChunk, WaveformTTSProvider

logger = logging.getLogger(__name__)

PipelineFactory = Callable[..., Any]


@contextmanager
def _offline_hub(enabled: bool) -> Iterator[None]:
    """Enable Hub offline mode without leaking process-wide state after loading.

    Transformers currently forwards a top-level ``local_files_only`` kwarg to
    the pipeline constructor after using it for downloads, where task-specific
    pipelines reject it. The Hub's official offline switch avoids that API bug.
    """
    if not enabled:
        yield
        return
    from huggingface_hub import constants

    previous_env = os.environ.get("HF_HUB_OFFLINE")
    previous_constant = constants.HF_HUB_OFFLINE
    os.environ["HF_HUB_OFFLINE"] = "1"
    constants.HF_HUB_OFFLINE = True
    try:
        yield
    finally:
        constants.HF_HUB_OFFLINE = previous_constant
        if previous_env is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous_env


def _transformers_pipeline(**kwargs: Any) -> Any:
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise TTSGenerationError(
            "Hugging Face support is not installed. Run: "
            "pip install 'epub-listener[huggingface]'"
        ) from exc
    return pipeline(**kwargs)


def resolve_device(requested: str) -> str | int:
    """Resolve ``auto`` to the fastest available PyTorch device."""
    if requested != "auto":
        return requested
    try:
        import torch
    except ImportError:
        return -1
    if torch.cuda.is_available():
        return 0
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return -1


def _load_speaker_embedding(path: Path) -> Any:
    """Load a SpeechT5-style speaker embedding from .npy or JSON."""
    try:
        if path.suffix.lower() == ".npy":
            values = np.load(path, allow_pickle=False)
        elif path.suffix.lower() == ".json":
            import json

            values = np.asarray(json.loads(path.read_text(encoding="utf-8")), dtype=np.float32)
        else:
            raise TTSGenerationError("Speaker embedding must be a .npy or .json file")
        values = np.asarray(values, dtype=np.float32)
    except TTSGenerationError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise TTSGenerationError(f"Could not load speaker embedding {path}: {exc}") from exc
    if values.ndim == 1:
        values = values[np.newaxis, :]
    if values.ndim != 2 or values.size == 0 or not np.isfinite(values).all():
        raise TTSGenerationError("Speaker embedding must be a finite one- or two-dimensional array")
    try:
        import torch
    except ImportError as exc:
        raise TTSGenerationError("PyTorch is required for Hugging Face speaker embeddings") from exc
    return torch.from_numpy(values)


class HuggingFaceTTSProvider(WaveformTTSProvider):
    """Run any model supported by Transformers' ``text-to-speech`` pipeline.

    Model repositories and local model directories use the same interface.
    Namespaced options expose pipeline preprocessing, forward, and generation
    arguments without coupling this project to individual model architectures.
    """

    def __init__(
        self,
        *,
        model: str,
        revision: str | None = None,
        device: str = "auto",
        dtype: str = "auto",
        trust_remote_code: bool = False,
        local_files_only: bool = False,
        model_options: Mapping[str, Mapping[str, Any]] | None = None,
        speaker_embedding: Path | None = None,
        chunk_chars: int = 500,
        chunk_pause_ms: int = 80,
        pipeline_factory: PipelineFactory | None = None,
    ) -> None:
        model_path = Path(model)
        display_model = (
            model_path.name if model_path.is_absolute() or model_path.exists() else model
        )
        super().__init__(
            engine_name=f"huggingface:{display_model}",
            chunk_chars=chunk_chars,
            chunk_pause_ms=chunk_pause_ms,
        )
        self.model = model
        self.revision = revision
        self.device = device
        self.dtype = dtype
        self.trust_remote_code = trust_remote_code
        self.local_files_only = local_files_only
        options = model_options or {}
        self.pipeline_options = dict(options.get("pipeline", {}))
        self.preprocess_options = dict(options.get("preprocess", {}))
        self.forward_options = dict(options.get("forward", {}))
        self.generate_options = dict(options.get("generate", {}))
        self.speaker_embedding = speaker_embedding
        self._pipeline_factory = pipeline_factory or _transformers_pipeline
        self._pipeline: Any | None = None
        self._speaker_tensor: Any | None = None

    def _get_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        kwargs = dict(self.pipeline_options)
        device_map = kwargs.get("device_map")
        if device_map is None:
            kwargs["device"] = resolve_device(self.device)
        kwargs.update(
            {
                "task": "text-to-speech",
                "model": self.model,
                "revision": self.revision,
                "dtype": self.dtype,
                "trust_remote_code": self.trust_remote_code,
            }
        )
        try:
            logger.info("Loading Hugging Face TTS model %s", self.model)
            with _offline_hub(self.local_files_only):
                self._pipeline = self._pipeline_factory(**kwargs)
        except TTSGenerationError:
            raise
        except Exception as exc:
            hint = (
                " Review the model card for required packages/options; use "
                "--trust-remote-code only after reviewing the repository."
            )
            raise TTSGenerationError(
                f"Could not load Hugging Face model {self.model}: {exc}.{hint}"
            ) from exc
        return self._pipeline

    def _forward_params(self) -> dict[str, Any]:
        params = dict(self.forward_options)
        if self.speaker_embedding is not None:
            if self._speaker_tensor is None:
                self._speaker_tensor = _load_speaker_embedding(self.speaker_embedding)
            params.setdefault("speaker_embeddings", self._speaker_tensor)
        return params

    def synthesize_chunk(
        self,
        text: str,
        voice: str | None,
        *,
        work_dir: Path,
        chunk_index: int,
    ) -> AudioChunk:
        del work_dir, chunk_index
        pipeline = self._get_pipeline()
        preprocess = dict(self.preprocess_options)
        forward = self._forward_params()
        model_type = str(
            getattr(getattr(getattr(pipeline, "model", None), "config", None), "model_type", "")
        )
        # A generic voice kwarg does not exist across Transformers models.
        # Apply the friendly shortcut only where its meaning is unambiguous.
        if voice and model_type == "bark":
            preprocess.setdefault("voice_preset", voice)
        elif voice and model_type == "vits" and "speaker_id" not in forward:
            try:
                forward["speaker_id"] = int(voice)
            except ValueError as exc:
                raise TTSGenerationError(
                    "VITS --voice must be a numeric speaker ID; otherwise pass the "
                    "model's required value through --model-options"
                ) from exc
        elif voice and not any(
            key in self.preprocess_options or key in self.forward_options
            for key in ("voice", "voice_preset", "speaker", "speaker_id", "speaker_embeddings")
        ):
            raise TTSGenerationError(
                f"Hugging Face model type '{model_type or 'unknown'}' has no generic --voice "
                "mapping; use --model-options documented by its model card"
            )
        try:
            result = pipeline(
                text,
                preprocess_params=preprocess,
                forward_params=forward,
                generate_kwargs=dict(self.generate_options),
            )
        except TTSGenerationError:
            raise
        except Exception as exc:
            raise TTSGenerationError(
                f"Hugging Face model {self.model} failed to synthesize a text chunk: {exc}"
            ) from exc
        if not isinstance(result, Mapping):
            raise TTSGenerationError(
                f"Hugging Face model {self.model} returned {type(result).__name__}; "
                "expected an audio mapping"
            )
        if "audio" not in result or "sampling_rate" not in result:
            raise TTSGenerationError(
                f"Hugging Face model {self.model} did not return audio and sampling_rate"
            )
        return AudioChunk(result["audio"], result["sampling_rate"])
