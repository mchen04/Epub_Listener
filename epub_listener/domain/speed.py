"""Playback-speed format, shared between config validation and TTS normalization."""

import re

SPEED_PATTERN = re.compile(r"[+-]?\d+%")
MIN_SPEED_MULTIPLIER = 0.1
MAX_SPEED_MULTIPLIER = 16.0


def is_valid_speed(value: str) -> bool:
    """True if ``value`` is a well-formed speed modifier such as ``+10%`` or ``-20%``."""
    return SPEED_PATTERN.fullmatch(value.strip()) is not None


def speed_to_multiplier(value: str) -> float:
    """Convert a validated percentage modifier to a positive playback rate.

    ``-100%`` and lower are rejected because they would stop or reverse time,
    which neither TTS engines nor ffmpeg can represent.
    """
    cleaned = value.strip()
    if not is_valid_speed(cleaned):
        raise ValueError("Speed must be like +10% or -20%")
    percentage = int(cleaned.removesuffix("%"))
    if not -90 <= percentage <= 1500:
        raise ValueError("Speed must be between -90% and +1500%")
    return (100 + percentage) / 100.0
