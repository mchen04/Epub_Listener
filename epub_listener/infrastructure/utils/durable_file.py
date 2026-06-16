"""Durable file replacement helpers."""

import os
import tempfile
from pathlib import Path


def fsync_path(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durably_replace(tmp_path: Path, output_path: Path) -> None:
    fsync_path(tmp_path)
    os.replace(tmp_path, output_path)
    fsync_path(output_path.parent)


def write_text_durably(
    output_path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    prefix: str | None = None,
    suffix: str = ".tmp",
) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding=encoding,
            dir=output_path.parent,
            prefix=prefix or f".{output_path.name}.",
            suffix=suffix,
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        durably_replace(tmp_path, output_path)
    except OSError:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
        raise
