"""FFMETADATA1 chapter metadata builder."""

from pathlib import Path

from epub_listener.application.ports import MetadataBuilder
from epub_listener.domain.models import AudioSegment


class FFmpegMetadataBuilder(MetadataBuilder):
    """Generates FFMETADATA1 files for chapter navigation."""

    def build(
        self,
        segments: list[AudioSegment],
        chapter_titles: dict[str, str],
        book_title: str,
        book_author: str,
        output: Path,
    ) -> Path:
        """Write metadata file and return its path."""
        with open(output, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            f.write(f"title={book_title}\n")
            f.write(f"artist={book_author}\n")
            f.write(f"album_artist={book_author}\n")
            f.write(f"album={book_title}\n\n")

            current_time_ms = 0
            for seg in segments:
                if seg.duration_ms <= 0:
                    continue
                start = current_time_ms
                end = current_time_ms + seg.duration_ms
                title = chapter_titles.get(seg.chapter_id, "Unknown")

                f.write("[CHAPTER]\n")
                f.write("TIMEBASE=1/1000\n")
                f.write(f"START={int(start)}\n")
                f.write(f"END={int(end)}\n")
                f.write(f"title={title}\n\n")

                current_time_ms = end

        return output
