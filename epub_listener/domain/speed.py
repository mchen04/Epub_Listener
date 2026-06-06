"""Playback-speed format, shared between config validation and TTS normalization."""

import re

SPEED_PATTERN = re.compile(r"[+-]?\d+%")


def is_valid_speed(value: str) -> bool:
    """True if ``value`` is a well-formed speed modifier such as ``+10%`` or ``-20%``."""
    return SPEED_PATTERN.fullmatch(value.strip()) is not None
