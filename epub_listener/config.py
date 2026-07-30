"""Application configuration via Pydantic."""

import os
from importlib.util import find_spec
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from epub_listener.concurrency import ConcurrencyStrategy
from epub_listener.domain.sanitize import sanitize_filename
from epub_listener.domain.speed import is_valid_speed

DEFAULT_KOKORO_PRESET = "ship-q8"

# Distilled presets: ~10M active params, far faster but single-voice
# (af_heart), fixed-speed, and unable to report per-token timestamps.
STUDENT_KOKORO_PRESETS = frozenset({"student", "student-fast", "student-exact-prosody"})
STUDENT_PRESET_VOICE = "af_heart"


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
    voice: str | None = Field(default=None, description="Edge-TTS voice identifier")
    author: str = Field(default="Michael Chen", description="Audiobook author metadata")
    title: str | None = Field(default=None, description="Audiobook title metadata override")
    resume_dir: Path | None = Field(default=None, description="Directory to resume from")
    use_kokoro: bool = Field(default=False, description="Use local Kokoro TTS")
    kokoro_voice: str | None = Field(default=None, description="Kokoro voice identifier")
    kokoro_hybrid_mps: bool = Field(
        default=False,
        description="Use one Apple MPS and one CPU Kokoro worker",
    )
    kokoro_mlx: bool = Field(
        default=False,
        description="Use Apple MLX Kokoro inference",
    )
    kokoro_preset: str | None = Field(
        default=None,
        description="FastKokoro model preset (e.g. ship-q8, exact, student-fast)",
    )
    transcript: bool = Field(
        default=True,
        description="Capture word timings and embed a read-along transcript in the MP3",
    )
    concurrency: ConcurrencyStrategy = Field(default="auto", description="Concurrency strategy")
    max_workers: int = Field(default=4, description="Maximum concurrent TTS jobs")
    log_level: str = Field(default="INFO", description="Logging level")

    @field_validator("speed")
    @classmethod
    def validate_speed(cls, v: str) -> str:
        if not is_valid_speed(v):
            raise ValueError("Speed must be like +10% or -20%")
        return v.strip()

    @field_validator("input_epub")
    @classmethod
    def validate_input(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"Input file not found: {v}")
        return v

    @field_validator("max_workers")
    @classmethod
    def validate_max_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_workers must be at least 1")
        return v

    @model_validator(mode="after")
    def validate_student_preset_limits(self) -> "Settings":
        """Reject options a distilled preset cannot honor.

        These would otherwise fail silently or mid-build: the engine ignores
        any requested voice, and raises on a non-1.0 speed only once the first
        chapter is synthesized.
        """
        preset = self.resolved_kokoro_preset
        if preset is None or preset not in STUDENT_KOKORO_PRESETS:
            return self
        if self.speed != "+0%":
            raise ValueError(
                f"Kokoro preset '{preset}' supports only --speed +0% (got {self.speed})"
            )
        voice = self.kokoro_voice
        if voice is not None and voice != STUDENT_PRESET_VOICE:
            raise ValueError(
                f"Kokoro preset '{preset}' is single-voice; "
                f"use --kokoro-voice {STUDENT_PRESET_VOICE} (got {voice})"
            )
        return self

    @property
    def resolved_kokoro_preset(self) -> str | None:
        """The FastKokoro preset in effect, or None when the engine is unavailable.

        The CLI flag wins over the legacy EPUB_KOKORO_PRESET environment
        variable. Returns None when fastkoko is not importable, since the
        provider then falls back to mlx-audio and the preset is meaningless.
        """
        if not (self.use_kokoro and self.kokoro_mlx):
            return None
        if find_spec("fastkoko") is None:
            return None
        env = os.environ.get("EPUB_KOKORO_PRESET", "").strip()
        return self.kokoro_preset or env or DEFAULT_KOKORO_PRESET

    @property
    def resolved_voice(self) -> str | None:
        """The voice to use for the active TTS backend."""
        return self.kokoro_voice if self.use_kokoro else self.voice

    @property
    def tts_backend(self) -> str:
        """The active TTS backend identifier used for resume cache compatibility.

        The preset is part of the identity: each one produces audibly
        different audio, so resuming under a different preset must invalidate
        cached chapters rather than splice two models into one audiobook.
        """
        if self.use_kokoro and self.kokoro_mlx:
            preset = self.resolved_kokoro_preset
            if preset is None:
                # mlx-audio fallback, which applies the +2.7 dB gain hack.
                return "kokoro-mlx-gain+2.7db"
            return f"kokoro-mlx-{preset}"
        return "kokoro" if self.use_kokoro else "edge"

    def resolve_output_path(self) -> Path:
        """Determine the final output path from an explicit path or an auto-generated name.

        Pure: computes the path without creating directories (the orchestrator
        owns directory creation).
        """
        if self.output_path:
            return self.output_path
        safe = sanitize_filename(self.input_epub.stem)
        return self.output_dir / f"{safe}_audiobook.mp3"
