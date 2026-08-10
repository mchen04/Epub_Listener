"""Infrastructure TTS provider protocols."""

from typing import Protocol

from epub_listener.application.ports import TTSJob


class TTSProvider(Protocol):
    """Generates one audio file and returns its final duration."""

    def run_job(self, job: TTSJob) -> int:
        """Generate one job (including optional transcript capture) synchronously."""
        ...
