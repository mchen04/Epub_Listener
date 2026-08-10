"""TTS speed normalization utilities."""

from epub_listener.domain.speed import is_valid_speed


def infer_kokoro_lang_for_voice(voice: str) -> str:
    """Return Kokoro's English language code for a voice identifier."""
    return "b" if voice.startswith("b") else "a"


def normalize_edge_speed(speed: str) -> str:
    """Validate and return an Edge-TTS rate string.

    Accepts forms like '+10%', '-20%', '+0%'.
    Edge-TTS expects a single trailing percent (e.g. '+10%').
    """
    cleaned = speed.strip()
    return cleaned if is_valid_speed(cleaned) else "+0%"
