"""Domain exceptions."""


class EpubListenerError(Exception):
    """Base exception for all Epub Listener errors."""


class ConfigurationError(EpubListenerError):
    """Invalid configuration or CLI arguments."""


class ParseError(EpubListenerError):
    """Failed to parse EPUB or extract chapters."""


class TTSGenerationError(EpubListenerError):
    """Failed to generate audio for a chapter."""


class AssemblyError(EpubListenerError):
    """Failed to assemble final audiobook with ffmpeg."""


class AudioProbeError(EpubListenerError):
    """Failed to probe audio file duration."""


class ResumeError(EpubListenerError):
    """Failed to resume a previous build."""


class TranscriptError(EpubListenerError):
    """Invalid or unusable transcript data."""
