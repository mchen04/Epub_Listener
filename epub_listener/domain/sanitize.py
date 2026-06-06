"""Pure filename sanitization (dependency-free, importable by any layer)."""

import re


def sanitize_filename(name: str) -> str:
    """Make a string safe for filesystem use.

    Keeps alphanumerics, spaces, underscores, and hyphens; collapses runs of
    whitespace; falls back to ``"unnamed"`` when nothing usable remains.
    """
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-"))
    safe = re.sub(r"\s+", " ", safe).strip()
    return safe or "unnamed"
