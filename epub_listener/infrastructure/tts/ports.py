"""Infrastructure TTS provider protocols."""

from pathlib import Path
from typing import Protocol

from epub_listener.application.ports import TTSJob


class TTSProvider(Protocol):
    """Generates one audio file and returns its final duration."""

    def generate(self, text: str, output: Path, voice: str | None, speed: str) -> int:
        """Generate one audio file and return its positive duration in milliseconds."""
        ...

    def run_job(self, job: TTSJob) -> int:
        """Generate one job (including optional transcript capture) synchronously."""
        ...
