"""FFMETADATA1 chapter metadata builder."""

import logging
from pathlib import Path

from epub_listener.application.ports import MetadataBuilder
from epub_listener.domain.exceptions import AssemblyError
from epub_listener.domain.models import AudioSegment

logger = logging.getLogger(__name__)


def _escape_ffmeta(value: str) -> str:
    """Escape special characters in FFMETADATA1 key values.

    Per the spec: backslash, equals, semicolon, hash, and newlines must be
    escaped; carriage returns are stripped.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("\r", "")
        .replace("\n", "\\\n")
    )


class FFmpegMetadataBuilder(MetadataBuilder):
    """Generates FFMETADATA1 files for chapter navigation."""

    def build(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        book_title: str,
        book_author: str,
        output: Path,
    ) -> None:
        """Write the FFMETADATA1 file to ``output``."""
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with open(output, "w", encoding="utf-8") as f:
                f.write(";FFMETADATA1\n")
                f.write(f"title={_escape_ffmeta(book_title)}\n")
                f.write(f"artist={_escape_ffmeta(book_author)}\n")
                f.write(f"album_artist={_escape_ffmeta(book_author)}\n")
                f.write(f"album={_escape_ffmeta(book_title)}\n\n")

                current_time_ms = 0
                for seg in segments:
                    if seg.duration_ms <= 0:
                        logger.warning(
                            "Skipping chapter %s in metadata: non-positive duration.",
                            seg.chapter_id,
                        )
                        continue
                    start = current_time_ms
                    end = current_time_ms + seg.duration_ms
                    title = chapter_titles.get(seg.chapter_id, "Unknown")

                    f.write("[CHAPTER]\n")
                    f.write("TIMEBASE=1/1000\n")
                    f.write(f"START={int(start)}\n")
                    f.write(f"END={int(end)}\n")
                    f.write(f"title={_escape_ffmeta(title)}\n\n")

                    current_time_ms = end
        except OSError as exc:
            raise AssemblyError(f"Could not write metadata file {output}: {exc}") from exc
