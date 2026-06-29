"""FFmpeg-based media assembler."""

import logging
import os
from pathlib import Path

from epub_listener.application.ports import MediaAssembler
from epub_listener.domain.exceptions import AssemblyError, AudioProbeError
from epub_listener.domain.models import AudioSegment
from epub_listener.infrastructure.utils.audio_probe import get_audio_duration_ms
from epub_listener.infrastructure.utils.durable_file import durably_replace
from epub_listener.infrastructure.utils.ffmpeg_runner import run_ffmpeg

logger = logging.getLogger(__name__)


def _escape_ffconcat_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", "'\\''")


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
        tmp_output = output.with_name(f".{output.stem}.tmp{output.suffix}")
        try:
            try:
                tmp_output.unlink(missing_ok=True)
                with open(concat_list, "w", encoding="utf-8") as f:
                    for seg in segments:
                        f.write(f"file '{_escape_ffconcat_path(seg.path.resolve())}'\n")
            except OSError as exc:
                raise AssemblyError(f"Could not prepare concat list {concat_list}: {exc}") from exc

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
                tmp_output,
            )
            self._validate_output(tmp_output, output)
            try:
                durably_replace(tmp_output, output)
            except OSError as exc:
                raise AssemblyError(f"Could not replace final audiobook {output}: {exc}") from exc
            logger.info("Assembled final audiobook: %s", output)
        finally:
            if concat_list.exists():
                try:
                    os.remove(concat_list)
                except OSError as exc:
                    logger.warning("Could not remove concat list %s: %s", concat_list, exc)
            try:
                tmp_output.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not remove temp output %s: %s", tmp_output, exc)

    def _validate_output(self, tmp_output: Path, output: Path) -> None:
        if not tmp_output.exists() or tmp_output.stat().st_size == 0:
            raise AssemblyError(f"ffmpeg produced no output for {output}")
        try:
            duration_ms = get_audio_duration_ms(tmp_output)
        except AudioProbeError as exc:
            raise AssemblyError(f"Could not validate assembled audiobook {output}: {exc}") from exc
        if duration_ms <= 0:
            raise AssemblyError(f"ffmpeg produced invalid output for {output}")
