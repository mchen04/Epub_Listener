import builtins
from pathlib import Path
from typing import Any

import pytest

from epub_listener.domain.exceptions import AssemblyError, AudioProbeError
from epub_listener.domain.models import AudioSegment
from epub_listener.infrastructure.media import ffmpeg_assembler
from epub_listener.infrastructure.media.ffmpeg_assembler import (
    FFmpegMediaAssembler,
    _escape_ffconcat_path,
)


def _segment(tmp_path: Path) -> AudioSegment:
    path = tmp_path / "chapter.mp3"
    path.write_bytes(b"audio")
    return AudioSegment(path=path, duration_ms=1000, chapter_id="0000")


def _metadata(tmp_path: Path) -> Path:
    path = tmp_path / "ffmetadata.txt"
    path.write_text("metadata", encoding="utf-8")
    return path


def test_assembler_replaces_existing_output_only_after_valid_temp_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "book.mp3"
    output.write_bytes(b"old")
    tmp_output = tmp_path / ".book.tmp.mp3"
    events: list[tuple[object, ...]] = []

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        assert kwargs == {}
        target_arg = args[-1]
        assert isinstance(target_arg, Path)
        target = target_arg
        events.append(("run", target))
        target.write_bytes(b"new")

    def fake_get_audio_duration_ms(path: Path) -> int:
        events.append(("probe", path))
        return 1000

    def fake_durably_replace(source: Path, target: Path) -> None:
        events.append(("replace", source, target))
        target.write_bytes(source.read_bytes())
        source.unlink()

    monkeypatch.setattr(ffmpeg_assembler, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(ffmpeg_assembler, "get_audio_duration_ms", fake_get_audio_duration_ms)
    monkeypatch.setattr(ffmpeg_assembler, "durably_replace", fake_durably_replace)

    FFmpegMediaAssembler().assemble([_segment(tmp_path)], _metadata(tmp_path), output)

    assert output.read_bytes() == b"new"
    assert not tmp_output.exists()
    assert not (tmp_path / "concat_list.txt").exists()
    assert events == [
        ("run", tmp_output),
        ("probe", tmp_output),
        ("replace", tmp_output, output),
    ]


def test_ffconcat_escape_handles_apostrophe_paths() -> None:
    path = Path("/tmp/reader's/chapter.mp3")
    assert f"file '{_escape_ffconcat_path(path)}'" == "file '/tmp/reader'\\''s/chapter.mp3'"


def test_assembler_uses_ffconcat_escape_for_apostrophe_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment_dir = tmp_path / "reader's"
    segment_dir.mkdir()
    segment_path = segment_dir / "chapter.mp3"
    segment_path.write_bytes(b"audio")
    output = tmp_path / "book.mp3"
    concat_contents = ""

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        nonlocal concat_contents
        concat_arg = args[5]
        target_arg = args[-1]
        assert isinstance(concat_arg, Path)
        assert isinstance(target_arg, Path)
        concat_contents = concat_arg.read_text(encoding="utf-8")
        target_arg.write_bytes(b"new")

    monkeypatch.setattr(ffmpeg_assembler, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(ffmpeg_assembler, "get_audio_duration_ms", lambda path: 1000)
    monkeypatch.setattr(
        ffmpeg_assembler,
        "durably_replace",
        lambda source, target: target.write_bytes(source.read_bytes()),
    )

    FFmpegMediaAssembler().assemble(
        [AudioSegment(segment_path, duration_ms=1000, chapter_id="0000")],
        _metadata(tmp_path),
        output,
    )

    assert concat_contents == "file '/tmp/reader'\\''s/chapter.mp3'\n".replace(
        "/tmp/reader", str(tmp_path / "reader")
    )


def test_assembler_writes_absolute_segment_paths_for_concat_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segment_path = Path("relative-cache/chapter.mp3")
    output = tmp_path / "book.mp3"
    concat_contents = ""

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        nonlocal concat_contents
        concat_arg = args[5]
        target_arg = args[-1]
        assert isinstance(concat_arg, Path)
        assert isinstance(target_arg, Path)
        concat_contents = concat_arg.read_text(encoding="utf-8")
        target_arg.write_bytes(b"new")

    monkeypatch.setattr(ffmpeg_assembler, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(ffmpeg_assembler, "get_audio_duration_ms", lambda path: 1000)
    monkeypatch.setattr(
        ffmpeg_assembler,
        "durably_replace",
        lambda source, target: target.write_bytes(source.read_bytes()),
    )

    FFmpegMediaAssembler().assemble(
        [AudioSegment(segment_path, duration_ms=1000, chapter_id="0000")],
        _metadata(tmp_path),
        output,
    )

    assert concat_contents == f"file '{_escape_ffconcat_path(segment_path.resolve())}'\n"


def test_assembler_wraps_concat_list_write_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _metadata(tmp_path)
    concat_list = metadata.with_name("concat_list.txt")

    def fail_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == concat_list:
            raise OSError("disk full")
        return builtins.open(path, *args, **kwargs)

    monkeypatch.setattr(ffmpeg_assembler, "open", fail_open, raising=False)

    with pytest.raises(AssemblyError, match="Could not prepare concat list"):
        FFmpegMediaAssembler().assemble([_segment(tmp_path)], metadata, tmp_path / "book.mp3")


def test_assembler_preserves_existing_output_when_ffmpeg_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "book.mp3"
    output.write_bytes(b"old")
    tmp_output = tmp_path / ".book.tmp.mp3"

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        target_arg = args[-1]
        assert isinstance(target_arg, Path)
        target_arg.write_bytes(b"partial")
        raise AssemblyError("boom")

    monkeypatch.setattr(ffmpeg_assembler, "run_ffmpeg", fake_run_ffmpeg)

    with pytest.raises(AssemblyError, match="boom"):
        FFmpegMediaAssembler().assemble([_segment(tmp_path)], _metadata(tmp_path), output)

    assert output.read_bytes() == b"old"
    assert not tmp_output.exists()
    assert not (tmp_path / "concat_list.txt").exists()


@pytest.mark.parametrize(
    "probe_failure",
    [AudioProbeError("bad probe"), 0],
    ids=["probe-error", "zero-duration"],
)
def test_assembler_preserves_existing_output_when_validation_fails_after_temp_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_failure: AudioProbeError | int,
) -> None:
    output = tmp_path / "book.mp3"
    output.write_bytes(b"old")
    tmp_output = tmp_path / ".book.tmp.mp3"
    replace_called = False

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        target_arg = args[-1]
        assert isinstance(target_arg, Path)
        target_arg.write_bytes(b"new")

    def fake_get_audio_duration_ms(path: Path) -> int:
        if isinstance(probe_failure, AudioProbeError):
            raise probe_failure
        return probe_failure

    def fake_durably_replace(source: Path, target: Path) -> None:
        nonlocal replace_called
        replace_called = True

    monkeypatch.setattr(ffmpeg_assembler, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(ffmpeg_assembler, "get_audio_duration_ms", fake_get_audio_duration_ms)
    monkeypatch.setattr(ffmpeg_assembler, "durably_replace", fake_durably_replace)

    with pytest.raises(AssemblyError):
        FFmpegMediaAssembler().assemble([_segment(tmp_path)], _metadata(tmp_path), output)

    assert output.read_bytes() == b"old"
    assert not tmp_output.exists()
    assert not (tmp_path / "concat_list.txt").exists()
    assert not replace_called


@pytest.mark.parametrize("payload", [None, b""], ids=["missing-temp", "empty-temp"])
def test_assembler_preserves_existing_output_when_temp_output_is_missing_or_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes | None,
) -> None:
    output = tmp_path / "book.mp3"
    output.write_bytes(b"old")
    tmp_output = tmp_path / ".book.tmp.mp3"
    replace_called = False

    def fake_run_ffmpeg(*args: object, **kwargs: object) -> None:
        target_arg = args[-1]
        assert isinstance(target_arg, Path)
        if payload is not None:
            target_arg.write_bytes(payload)

    def fake_get_audio_duration_ms(path: Path) -> int:
        raise AssertionError("probe should not run before temp output is present")

    def fake_durably_replace(source: Path, target: Path) -> None:
        nonlocal replace_called
        replace_called = True

    monkeypatch.setattr(ffmpeg_assembler, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(ffmpeg_assembler, "get_audio_duration_ms", fake_get_audio_duration_ms)
    monkeypatch.setattr(ffmpeg_assembler, "durably_replace", fake_durably_replace)

    with pytest.raises(AssemblyError, match="produced no output"):
        FFmpegMediaAssembler().assemble([_segment(tmp_path)], _metadata(tmp_path), output)

    assert output.read_bytes() == b"old"
    assert not tmp_output.exists()
    assert not (tmp_path / "concat_list.txt").exists()
    assert not replace_called
