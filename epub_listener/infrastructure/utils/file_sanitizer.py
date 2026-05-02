"""Filename sanitization."""

import re


class FileSanitizer:
    """Sanitizes strings for safe filesystem usage."""

    def sanitize(self, name: str) -> str:
        """Remove or replace characters unsafe for filenames.

        Keeps alphanumeric, spaces, underscores, and hyphens.
        Collapses multiple spaces and strips trailing whitespace.
        """
        safe = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-"))
        safe = re.sub(r"\s+", " ", safe).strip()
        return safe or "unnamed"
