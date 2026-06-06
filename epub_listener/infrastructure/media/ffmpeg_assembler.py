"""FFmpeg-based media assembler."""

import logging
import os
from pathlib import Path

from epub_listener.application.ports import MediaAssembler
from epub_listener.domain.exceptions import AssemblyError
from epub_listener.domain.models import AudioSegment
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg

logger = logging.getLogger(__name__)


class FFmpegMediaAssembler(MediaAssembler):
    """Concatenates MP3 segments and embeds metadata using ffmpeg."""

    def assemble(
        self,
        segments: list[AudioSegment],
        metadata_path: Path,
        output: Path,
    ) -> None:
        """Merge segments into the final output. Raises AssemblyError on failure."""
        if not segments:
            raise AssemblyError("No audio segments to assemble.")

        concat_list = metadata_path.with_name("concat_list.txt")
        try:
            with open(concat_list, "w", encoding="utf-8") as f:
                for seg in segments:
                    escaped = str(seg.path).replace("\\", "\\\\").replace("'", "\\'")
                    f.write(f"file '{escaped}'\n")

            run_ffmpeg(
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list,
                "-i",
                metadata_path,
                "-map_metadata",
                "1",
                "-c:a",
                "copy",
                "-write_id3v1",
                "1",
                "-id3v2_version",
                "3",
                output,
            )
            logger.info("Assembled final audiobook: %s", output)
        finally:
            if concat_list.exists():
                try:
                    os.remove(concat_list)
                except OSError as exc:
                    logger.warning("Could not remove concat list %s: %s", concat_list, exc)
