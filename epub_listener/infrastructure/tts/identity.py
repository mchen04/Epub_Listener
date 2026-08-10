"""Privacy-preserving resume identities for configurable local models."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any


def fingerprint(payload: dict[str, Any]) -> str:
    """Return a short deterministic SHA-256 fingerprint of JSON-compatible settings."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def safe_model_label(value: str) -> str:
    """Readable model label that never exposes an absolute local path."""
    path = Path(value)
    if path.is_absolute() or path.exists():
        value = path.name
    cleaned = re.sub(r"[^A-Za-z0-9._/-]+", "_", value).strip("_")
    return cleaned[-96:] or "model"


def file_sha256(path: Path) -> str:
    """Hash a small identity artifact such as a speaker embedding."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def path_signature(path: Path) -> str | None:
    """Efficiently fingerprint a local model/executable without hashing huge weights."""
    path = path.expanduser()
    if not path.exists():
        return None
    digest = hashlib.sha256()
    root = path.resolve()
    candidates: list[Path]
    if root.is_file():
        candidates = [root]
    else:
        candidates = []
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                name for name in dirnames if name not in {".git", ".cache", "__pycache__"}
            )
            candidates.extend(Path(directory) / name for name in sorted(filenames))
            if len(candidates) > 100_000:
                digest.update(b"file-count-truncated")
                break
    for item in candidates:
        try:
            stat = item.stat()
            relative = item.name if root.is_file() else str(item.relative_to(root))
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(f"\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode())
            with item.open("rb") as handle:
                if stat.st_size <= 128 * 1024:
                    digest.update(handle.read())
                else:
                    digest.update(handle.read(64 * 1024))
                    handle.seek(max(0, stat.st_size - 64 * 1024))
                    digest.update(handle.read(64 * 1024))
        except OSError:
            digest.update(f"unreadable:{item}".encode("utf-8", errors="surrogateescape"))
    return digest.hexdigest()[:16]


def command_dependency_signatures(template: str) -> dict[str, str]:
    """Fingerprint the executable and file arguments present in a command template."""
    try:
        argv = shlex.split(template)
    except ValueError:
        return {"template": "invalid"}
    paths: set[Path] = set()
    if argv:
        executable = shutil.which(argv[0])
        if executable:
            paths.add(Path(executable))
    for argument in argv[1:]:
        candidate = argument.split("=", 1)[-1] if argument.startswith("--") else argument
        if "{" in candidate or "}" in candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            paths.add(path.resolve())
    return {
        str(path): signature
        for path in sorted(paths, key=str)
        if (signature := path_signature(path)) is not None
    }
