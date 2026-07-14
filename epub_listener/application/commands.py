"""Application command models."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildAudiobookCommand:
    input_epub: Path
    output_path: Path
    author: str
    voice: str | None
    speed: str
    temp_dir: Path
    title: str | None = None
    tts_backend: str = "edge"

    @property
    def generation_key(self) -> str:
        voice = self.voice or ""
        return f"tts_backend={self.tts_backend}\nvoice={voice}\nspeed={self.speed}"
