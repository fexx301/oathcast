"""Durable local writes for generated evidence and report artifacts."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import stat
import tempfile


ArtifactValidator = Callable[[Path], object]


def _fsync_directory(path: Path) -> None:
    """Persist a completed rename on filesystems that support directory fsync."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Some platforms/filesystems reject directory fsync. The file itself
        # has still been flushed before the atomic replace.
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    validate: ArtifactValidator | None = None,
    default_mode: int = 0o644,
) -> None:
    """Write bytes through a unique, flushed sibling and atomically replace path."""

    if not isinstance(data, bytes):
        raise TypeError("artifact data must be bytes")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        target_mode = default_mode

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, target_mode)
        if validate is not None:
            validate(temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    validate: ArtifactValidator | None = None,
    default_mode: int = 0o644,
) -> None:
    """Encode and atomically persist one text artifact."""

    if not isinstance(text, str):
        raise TypeError("artifact text must be a string")
    atomic_write_bytes(
        path,
        text.encode(encoding),
        validate=validate,
        default_mode=default_mode,
    )
