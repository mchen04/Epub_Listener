"""Application configuration via Pydantic."""

from __future__ import annotations

import json
import os
import re
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from epub_listener.concurrency import ConcurrencyStrategy
from epub_listener.domain.sanitize import sanitize_filename
from epub_listener.domain.speed import speed_to_multiplier
from epub_listener.domain.tts import ModelDType, TTSEngine
from epub_listener.infrastructure.tts.identity import (
    command_dependency_signatures,
    file_sha256,
    fingerprint,
    path_signature,
    safe_model_label,
)

DEFAULT_KOKORO_PRESET = "ship-q8"

# Distilled presets: ~10M active params, far faster but single-voice
# (af_heart), fixed-speed, and unable to report per-token timestamps.
STUDENT_KOKORO_PRESETS = frozenset({"student", "student-fast", "student-exact-prosody"})
STUDENT_PRESET_VOICE = "af_heart"

_DEVICE_PATTERN = re.compile(r"[A-Za-z0-9_.:-]+")
_MODEL_OPTION_GROUPS = frozenset({"pipeline", "preprocess", "forward", "generate"})
_RESERVED_PIPELINE_OPTIONS = frozenset(
    {"task", "model", "revision", "device", "dtype", "trust_remote_code", "local_files_only"}
)


class Settings(BaseSettings):
    """Build configuration from CLI args and environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="EPUB_LISTENER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    input_epub: Path = Field(description="Path to the input EPUB file")
    output_path: Path | None = Field(default=None, description="Explicit output .mp3 path")
    output_dir: Path = Field(
        default=Path("outputs"), description="Directory for generated audiobooks"
    )
    speed: str = Field(default="+0%", description="Playback speed modifier (e.g., +10%, -20%)")
    voice: str | None = Field(default=None, description="Voice or voice-preset identifier")
    author: str = Field(default="Michael Chen", description="Audiobook author metadata")
    title: str | None = Field(default=None, description="Audiobook title metadata override")
    resume_dir: Path | None = Field(default=None, description="Directory to resume from")

    # Unified engine settings. ``None`` preserves the old Edge default while
    # allowing legacy --use-kokoro flags to resolve without ambiguous state.
    engine: TTSEngine | None = Field(default=None, description="TTS engine")
    model: str | None = Field(default=None, description="Hugging Face model ID or local path")
    revision: str | None = Field(default=None, description="Pinned Hugging Face model revision")
    device: str = Field(default="auto", description="Inference device")
    dtype: ModelDType = Field(default="auto", description="Inference precision")
    trust_remote_code: bool = Field(
        default=False,
        description="Allow reviewed custom Python code from a Hugging Face repository",
    )
    local_files_only: bool = Field(default=False, description="Disable Hugging Face downloads")
    model_options: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Namespaced Hugging Face pipeline/call options",
    )
    speaker_embedding: Path | None = Field(
        default=None,
        description="SpeechT5-style .npy or .json speaker embedding",
    )
    chunk_chars: int = Field(
        default=500,
        description="Maximum characters per local-model inference call; 0 disables chunking",
    )
    chunk_pause_ms: int = Field(default=80, description="Silence inserted between model chunks")

    # Language-neutral local executable adapter.
    model_command: str | None = Field(
        default=None,
        description="Local TTS command template containing {output}",
    )
    command_output_format: Literal["wav", "mp3", "flac", "ogg"] = "wav"
    model_timeout: int = Field(default=1800, description="Per-chunk local command timeout")

    # Backward-compatible Kokoro flags. New invocations should use --engine.
    use_kokoro: bool = Field(default=False, description="Legacy alias for --engine kokoro")
    kokoro_voice: str | None = Field(default=None, description="Legacy Kokoro voice alias")
    kokoro_hybrid_mps: bool = Field(
        default=False,
        description="Use one Apple MPS and one CPU Kokoro worker",
    )
    kokoro_mlx: bool = Field(default=False, description="Use Apple MLX Kokoro inference")
    kokoro_preset: str | None = Field(
        default=None,
        description="FastKokoro model preset (e.g. ship-q8, exact, student-fast)",
    )

    transcript: bool = Field(
        default=True,
        description="Capture timings and embed a read-along transcript in the MP3",
    )
    concurrency: ConcurrencyStrategy = Field(default="auto", description="Concurrency strategy")
    max_workers: int = Field(default=4, description="Maximum concurrent TTS jobs")
    log_level: str = Field(default="INFO", description="Logging level")

    @field_validator("speed")
    @classmethod
    def validate_speed(cls, value: str) -> str:
        try:
            speed_to_multiplier(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(str(exc)) from exc
        return value.strip()

    @field_validator("input_epub")
    @classmethod
    def validate_input(cls, value: Path) -> Path:
        if not value.exists():
            raise ValueError(f"Input file not found: {value}")
        if not value.is_file():
            raise ValueError(f"Input path is not a file: {value}")
        if value.suffix.lower() != ".epub":
            raise ValueError(f"Input file must end with .epub: {value}")
        return value

    @field_validator("max_workers")
    @classmethod
    def validate_max_workers(cls, value: int) -> int:
        if not 1 <= value <= 64:
            raise ValueError("max_workers must be between 1 and 64")
        return value

    @field_validator("chunk_chars")
    @classmethod
    def validate_chunk_chars(cls, value: int) -> int:
        if value != 0 and not 100 <= value <= 100_000:
            raise ValueError("chunk_chars must be 0 or between 100 and 100000")
        return value

    @field_validator("chunk_pause_ms")
    @classmethod
    def validate_chunk_pause(cls, value: int) -> int:
        if not 0 <= value <= 10_000:
            raise ValueError("chunk_pause_ms must be between 0 and 10000")
        return value

    @field_validator("model_timeout")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if not 1 <= value <= 86_400:
            raise ValueError("model_timeout must be between 1 and 86400 seconds")
        return value

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or _DEVICE_PATTERN.fullmatch(cleaned) is None:
            raise ValueError("device must look like auto, cpu, mps, cuda, or cuda:0")
        return cleaned

    @field_validator("revision", "model_command", "voice", "kokoro_voice")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        if "\x00" in cleaned or "\n" in cleaned or "\r" in cleaned:
            raise ValueError("value must be a single line without NUL characters")
        return cleaned

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        cleaned = cls.validate_optional_text(value)
        if cleaned is None:
            return None
        local_path = Path(cleaned).expanduser()
        return str(local_path.resolve()) if local_path.exists() else cleaned

    @field_validator("speaker_embedding")
    @classmethod
    def validate_speaker_embedding(cls, value: Path | None) -> Path | None:
        if value is not None and (not value.exists() or not value.is_file()):
            raise ValueError(f"Speaker embedding file not found: {value}")
        return value

    @field_validator("model_options")
    @classmethod
    def validate_model_options(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        unknown = set(value) - _MODEL_OPTION_GROUPS
        if unknown:
            raise ValueError(
                "model_options supports only these groups: "
                + ", ".join(sorted(_MODEL_OPTION_GROUPS))
            )
        reserved = set(value.get("pipeline", {})) & _RESERVED_PIPELINE_OPTIONS
        if reserved:
            raise ValueError(
                "Use dedicated flags instead of reserved pipeline option(s): "
                + ", ".join(sorted(reserved))
            )
        try:
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"model_options must contain finite JSON values: {exc}") from exc
        return value

    @model_validator(mode="after")
    def validate_engine_configuration(self) -> Settings:
        engine = self.tts_engine
        legacy_engine = "kokoro-mlx" if self.kokoro_mlx else "kokoro"
        if self.use_kokoro and self.engine is not None and self.engine != legacy_engine:
            raise ValueError(
                f"--use-kokoro conflicts with --engine {self.engine}; use one engine selector"
            )
        if self.kokoro_mlx and engine != "kokoro-mlx":
            raise ValueError("--kokoro-mlx requires --use-kokoro or --engine kokoro-mlx")
        if self.kokoro_hybrid_mps and engine != "kokoro":
            raise ValueError("--kokoro-hybrid-mps requires the kokoro engine")
        if self.kokoro_preset and engine != "kokoro-mlx":
            raise ValueError("--kokoro-preset requires --engine kokoro-mlx")

        if engine == "huggingface":
            if self.model is None:
                raise ValueError("--engine huggingface requires --model")
            if self.model_command is not None:
                raise ValueError("--model-command is only valid with --engine command")
            if self.command_output_format != "wav" or self.model_timeout != 1800:
                raise ValueError("Command settings are only valid with --engine command")
        elif engine == "command":
            if self.model_command is None:
                raise ValueError("--engine command requires --model-command")
            if self.model is not None:
                raise ValueError("--model is only valid with --engine huggingface")
            if self.model_options or self.speaker_embedding is not None:
                raise ValueError("Hugging Face model options are not valid with --engine command")
            if self.revision is not None or self.device != "auto" or self.dtype != "auto":
                raise ValueError("Hugging Face loading options require --engine huggingface")
            if self.trust_remote_code or self.local_files_only:
                raise ValueError("Hugging Face security flags require --engine huggingface")
        else:
            if self.model is not None:
                raise ValueError("--model requires --engine huggingface")
            if self.model_command is not None:
                raise ValueError("--model-command requires --engine command")
            if self.model_options or self.speaker_embedding is not None:
                raise ValueError("Model options require --engine huggingface")
            if self.trust_remote_code or self.local_files_only:
                raise ValueError("Hugging Face security flags require --engine huggingface")
            if self.revision is not None or self.device != "auto" or self.dtype != "auto":
                raise ValueError("Hugging Face loading options require --engine huggingface")
            if self.chunk_chars != 500 or self.chunk_pause_ms != 80:
                raise ValueError("Chunk settings require --engine huggingface or command")
            if self.command_output_format != "wav" or self.model_timeout != 1800:
                raise ValueError("Command settings require --engine command")

        preset = self.requested_kokoro_preset
        if preset in STUDENT_KOKORO_PRESETS:
            if self.speed != "+0%":
                raise ValueError(
                    f"Kokoro preset '{preset}' supports only --speed +0% (got {self.speed})"
                )
            voice = self.kokoro_voice or self.voice
            if voice is not None and voice != STUDENT_PRESET_VOICE:
                raise ValueError(
                    f"Kokoro preset '{preset}' is single-voice; "
                    f"use --voice {STUDENT_PRESET_VOICE} (got {voice})"
                )
        return self

    @property
    def tts_engine(self) -> TTSEngine:
        if self.engine is not None:
            return self.engine
        if self.use_kokoro:
            return "kokoro-mlx" if self.kokoro_mlx else "kokoro"
        return "edge"

    @property
    def requested_kokoro_preset(self) -> str | None:
        if self.tts_engine != "kokoro-mlx":
            return None
        return self.kokoro_preset or os.environ.get("EPUB_KOKORO_PRESET", "").strip() or None

    @property
    def resolved_kokoro_preset(self) -> str | None:
        """The active FastKokoro preset, or None for the mlx-audio fallback."""
        requested = self.requested_kokoro_preset
        if requested is not None:
            return requested
        if self.tts_engine != "kokoro-mlx" or find_spec("fastkoko") is None:
            return None
        return DEFAULT_KOKORO_PRESET

    @property
    def resolved_voice(self) -> str | None:
        """The voice to use for the active TTS backend."""
        if self.tts_engine in {"kokoro", "kokoro-mlx"}:
            return self.kokoro_voice or self.voice
        return self.voice

    @property
    def tts_backend(self) -> str:
        """Output-affecting backend identity used for safe resume caching."""
        engine = self.tts_engine
        if engine == "kokoro-mlx":
            preset = self.resolved_kokoro_preset
            return f"kokoro-mlx-{preset}" if preset is not None else "kokoro-mlx-gain+2.7db"
        if engine == "huggingface":
            hf_payload: dict[str, Any] = {
                "model": self.model,
                "local_model": path_signature(Path(self.model)) if self.model else None,
                "revision": self.revision,
                "device": self.device,
                "dtype": self.dtype,
                "trust_remote_code": self.trust_remote_code,
                "local_files_only": self.local_files_only,
                "options": self.model_options,
                "speaker_embedding": self._speaker_embedding_digest(),
                "chunk_chars": self.chunk_chars,
                "chunk_pause_ms": self.chunk_pause_ms,
            }
            return (
                f"huggingface:{safe_model_label(self.model or 'model')}#{fingerprint(hf_payload)}"
            )
        if engine == "command":
            command_payload: dict[str, Any] = {
                "command": self.model_command,
                "dependencies": command_dependency_signatures(self.model_command or ""),
                "format": self.command_output_format,
                "chunk_chars": self.chunk_chars,
                "chunk_pause_ms": self.chunk_pause_ms,
            }
            return f"command#{fingerprint(command_payload)}"
        return engine

    def _speaker_embedding_digest(self) -> str | None:
        if self.speaker_embedding is None:
            return None
        try:
            return file_sha256(self.speaker_embedding)
        except OSError:
            return f"missing:{self.speaker_embedding}"

    def resolve_output_path(self) -> Path:
        """Determine the final output path without creating directories."""
        if self.output_path:
            return self.output_path
        safe = sanitize_filename(self.input_epub.stem)
        return self.output_dir / f"{safe}_audiobook.mp3"
