from pathlib import Path

import pytest

from epub_listener.domain.exceptions import AssemblyError
from epub_listener.domain.models import AudioSegment
from epub_listener.infrastructure.media.metadata_builder import FFmpegMetadataBuilder


def test_metadata_builder_escapes_special_metadata_values(tmp_path: Path) -> None:
    segment_path = tmp_path / "chapter.mp3"
    segment_path.write_bytes(b"audio")
    output = tmp_path / "ffmetadata.txt"

    FFmpegMetadataBuilder().build(
        [AudioSegment(segment_path, duration_ms=1000, chapter_id="0000")],
        {"0000": "Chapter=One; #Back\\slash\nNext"},
        "Book=Title; #Back\\slash\nNext",
        "Author=Name; #Back\\slash\nNext",
        output,
    )

    assert output.read_text(encoding="utf-8") == (
        ";FFMETADATA1\n"
        "title=Book\\=Title\\; \\#Back\\\\slash\\\n"
        "Next\n"
        "artist=Author\\=Name\\; \\#Back\\\\slash\\\n"
        "Next\n"
        "album_artist=Author\\=Name\\; \\#Back\\\\slash\\\n"
        "Next\n"
        "album=Book\\=Title\\; \\#Back\\\\slash\\\n"
        "Next\n\n"
        "[CHAPTER]\n"
        "TIMEBASE=1/1000\n"
        "START=0\n"
        "END=1000\n"
        "title=Chapter\\=One\\; \\#Back\\\\slash\\\n"
        "Next\n\n"
    )


def test_metadata_builder_wraps_output_directory_failures(tmp_path: Path) -> None:
    output_parent = tmp_path / "metadata-parent"
    output_parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(AssemblyError, match="Could not write metadata file"):
        FFmpegMetadataBuilder().build(
            [],
            {},
            "Book",
            "Author",
            output_parent / "ffmetadata.txt",
        )
