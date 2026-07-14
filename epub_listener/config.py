"""Application configuration via Pydantic."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from epub_listener.concurrency import ConcurrencyStrategy
from epub_listener.domain.sanitize import sanitize_filename
from epub_listener.domain.speed import is_valid_speed


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

    @property
    def resolved_voice(self) -> str | None:
        """The voice to use for the active TTS backend."""
        return self.kokoro_voice if self.use_kokoro else self.voice

    @property
    def tts_backend(self) -> str:
        """The active TTS backend identifier used for resume cache compatibility."""
        if self.use_kokoro and self.kokoro_mlx:
            return "kokoro-mlx-gain+2.7db"
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
