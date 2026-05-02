"""Application configuration via Pydantic."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    resume_dir: Path | None = Field(default=None, description="Directory to resume from")
    use_kokoro: bool = Field(default=False, description="Use local Kokoro TTS")
    kokoro_voice: str | None = Field(default=None, description="Kokoro voice identifier")
    kokoro_lang: str = Field(default="a", description="Kokoro language code")
    concurrency: Literal["sequential", "async", "parallel"] = Field(
        default="async", description="Concurrency strategy"
    )
    max_workers: int = Field(default=4, description="Max workers for parallel generation")
    log_level: str = Field(default="INFO", description="Logging level")

    @field_validator("speed")
    @classmethod
    def validate_speed(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[+-]?\d+%", v.strip()):
            raise ValueError("Speed must be like +10% or -20%")
        return v.strip()

    @field_validator("input_epub")
    @classmethod
    def validate_input(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"Input file not found: {v}")
        return v

    def resolve_output_path(self) -> Path:
        """Determine final output path from explicit path or auto-generated name."""
        if self.output_path:
            return self.output_path
        base = self.input_epub.stem
        safe = "".join(c for c in base if c.isalnum() or c in (" ", "_", "-")).rstrip()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir / f"{safe}_audiobook.mp3"
