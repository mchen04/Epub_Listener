from pathlib import Path

import pytest

from epub_listener.infrastructure.utils import durable_file


def test_durably_replace_fsyncs_temp_then_replaces_then_fsyncs_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_output = tmp_path / ".book.tmp.mp3"
    output = tmp_path / "book.mp3"
    tmp_output.write_bytes(b"new")
    events: list[tuple[object, ...]] = []
    fd_paths: dict[int, Path] = {}
    real_open = durable_file.os.open
    real_replace = durable_file.os.replace

    def fake_open(path: Path, flags: int) -> int:
        fd = real_open(path, flags)
        fd_paths[fd] = path
        return fd

    def fake_fsync(fd: int) -> None:
        events.append(("fsync", fd_paths[fd]))

    def fake_replace(source: Path, target: Path) -> None:
        events.append(("replace", source, target))
        real_replace(source, target)

    monkeypatch.setattr(durable_file.os, "open", fake_open)
    monkeypatch.setattr(durable_file.os, "fsync", fake_fsync)
    monkeypatch.setattr(durable_file.os, "replace", fake_replace)

    durable_file.durably_replace(tmp_output, output)

    assert output.read_bytes() == b"new"
    assert events == [
        ("fsync", tmp_output),
        ("replace", tmp_output, output),
        ("fsync", tmp_path),
    ]


def test_durably_replace_preserves_output_when_temp_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_output = tmp_path / ".book.tmp.mp3"
    output = tmp_path / "book.mp3"
    tmp_output.write_bytes(b"new")
    output.write_bytes(b"old")

    def fail_fsync(fd: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(durable_file.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        durable_file.durably_replace(tmp_output, output)

    assert output.read_bytes() == b"old"
    assert tmp_output.read_bytes() == b"new"


def test_durably_replace_preserves_output_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_output = tmp_path / ".book.tmp.mp3"
    output = tmp_path / "book.mp3"
    tmp_output.write_bytes(b"new")
    output.write_bytes(b"old")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(durable_file.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        durable_file.durably_replace(tmp_output, output)

    assert output.read_bytes() == b"old"
    assert tmp_output.read_bytes() == b"new"


def test_durably_replace_reports_directory_fsync_failure_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_output = tmp_path / ".book.tmp.mp3"
    output = tmp_path / "book.mp3"
    tmp_output.write_bytes(b"new")
    output.write_bytes(b"old")
    fd_paths: dict[int, Path] = {}
    real_open = durable_file.os.open

    def fake_open(path: Path, flags: int) -> int:
        fd = real_open(path, flags)
        fd_paths[fd] = path
        return fd

    def fail_directory_fsync(fd: int) -> None:
        if fd_paths[fd] == tmp_path:
            raise OSError("directory fsync failed")

    monkeypatch.setattr(durable_file.os, "open", fake_open)
    monkeypatch.setattr(durable_file.os, "fsync", fail_directory_fsync)

    with pytest.raises(OSError, match="directory fsync failed"):
        durable_file.durably_replace(tmp_output, output)

    assert output.read_bytes() == b"new"
    assert not tmp_output.exists()


def test_fsync_path_closes_descriptor_when_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    def fake_open(path: Path, flags: int) -> int:
        return 99

    def fail_fsync(fd: int) -> None:
        raise OSError("fsync failed")

    def fake_close(fd: int) -> None:
        closed.append(fd)

    monkeypatch.setattr(durable_file.os, "open", fake_open)
    monkeypatch.setattr(durable_file.os, "fsync", fail_fsync)
    monkeypatch.setattr(durable_file.os, "close", fake_close)

    with pytest.raises(OSError, match="fsync failed"):
        durable_file.fsync_path(tmp_path / "book.mp3")

    assert closed == [99]


def test_write_text_durably_fsyncs_temp_then_replaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "progress.json"
    events: list[tuple[object, ...]] = []

    def fake_fsync(fd: int) -> None:
        events.append(("fsync-temp", fd))

    def fake_durably_replace(source: Path, target: Path) -> None:
        events.append(("replace", source, target))
        assert source.parent == tmp_path
        assert source.read_text(encoding="utf-8") == '{"ok": true}\n'
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        source.unlink()

    monkeypatch.setattr(durable_file.os, "fsync", fake_fsync)
    monkeypatch.setattr(durable_file, "durably_replace", fake_durably_replace)

    durable_file.write_text_durably(output, '{"ok": true}\n', prefix=".progress.")

    assert output.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert events[0][0] == "fsync-temp"
    assert events[1][0] == "replace"
    assert list(tmp_path.glob(".progress.*.tmp")) == []


def test_write_text_durably_preserves_output_and_cleans_temp_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "progress.json"
    output.write_text("old", encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(durable_file, "durably_replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        durable_file.write_text_durably(output, "new", prefix=".progress.")

    assert output.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".progress.*.tmp")) == []


def test_write_text_durably_preserves_output_and_cleans_temp_when_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "progress.json"
    output.write_text("old", encoding="utf-8")

    def fail_fsync(fd: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(durable_file.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="fsync failed"):
        durable_file.write_text_durably(output, "new", prefix=".progress.")

    assert output.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".progress.*.tmp")) == []
