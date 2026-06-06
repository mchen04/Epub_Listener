"""TTS speed normalization utilities."""

from epub_listener.domain.speed import is_valid_speed


def normalize_edge_speed(speed: str) -> str:
    """Validate and return an Edge-TTS rate string.

    Accepts forms like '+10%', '-20%', '+0%'.
    Edge-TTS expects a single trailing percent (e.g. '+10%').
    """
    cleaned = speed.strip()
    return cleaned if is_valid_speed(cleaned) else "+0%"


def edge_speed_to_multiplier(speed: str) -> float:
    """Convert an Edge-TTS speed string to a float multiplier for Kokoro.

    Examples:
        '+10%%' -> 1.1
        '-20%%' -> 0.8
    """
    try:
        clean = speed.replace("%", "").strip()
        delta = float(clean)
        return 1.0 + (delta / 100.0)
    except (ValueError, TypeError):
        return 1.0
