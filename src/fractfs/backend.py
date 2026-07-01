"""Backend abstraction for the remote/durable store.

Two families, split by how the store is *reached* rather than by vendor:

* ``mount`` — anything reachable as a POSIX filesystem path: a Databricks Volume
  FUSE mount, NFS/EFS, an SMB share, or a plain local directory. All served by
  :class:`PosixBackend`, which needs no third-party dependencies.
* ``fsspec`` — anything reachable as an fsspec URL (S3, GCS, ADLS, ...), served by
  :class:`FsspecBackend`. Imported lazily so the base install stays dep-light.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class Backend(Protocol):
    """Minimal surface the provisioner and sync daemon need from a store."""

    def exists(self, path: str) -> bool: ...

    def makedirs(self, path: str) -> None: ...

    def put_file(self, local_path: os.PathLike, remote_path: str) -> None:
        """Copy a local file to ``remote_path``, atomically where possible."""

    def get_file(self, remote_path: str, local_path: os.PathLike) -> None:
        """Copy ``remote_path`` down to a local file, atomically where possible."""

    def list_files(self, path: str) -> Iterable[str]:
        """Yield remote-relative paths of every file under ``path`` (recursive)."""

    def remove(self, path: str) -> None: ...


class PosixBackend:
    """Backend over a POSIX-visible root (a mount — Volume/NFS/SMB — or a local dir).

    ``root`` is the absolute remote root; ``path`` arguments to every method are
    interpreted relative to it (or accepted as already-absolute paths under it).
    """

    def __init__(self, root: os.PathLike, *, atomic_rename: bool = True):
        self.root = Path(root)
        # FUSE mounts don't always honour atomic rename; callers can disable it
        # to fall back to a plain copy (see plan: FUSE atomicity open question).
        self.atomic_rename = atomic_rename

    def _abs(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.root / p

    def exists(self, path: str) -> bool:
        return self._abs(path).exists()

    def makedirs(self, path: str) -> None:
        self._abs(path).mkdir(parents=True, exist_ok=True)

    def put_file(self, local_path: os.PathLike, remote_path: str) -> None:
        dst = self._abs(remote_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(Path(local_path), dst)

    def get_file(self, remote_path: str, local_path: os.PathLike) -> None:
        dst = Path(local_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(self._abs(remote_path), dst)

    def list_files(self, path: str) -> Iterable[str]:
        base = self._abs(path)
        if not base.exists():
            return
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                full = Path(dirpath) / name
                yield str(full.relative_to(self.root))

    def remove(self, path: str) -> None:
        p = self._abs(path)
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()

    def _write_atomic(self, src: Path, dst: Path) -> None:
        """Copy ``src`` to ``dst`` via a temp file + rename when atomic_rename."""
        if not self.atomic_rename:
            shutil.copy2(src, dst)
            return
        tmp = dst.with_name(f".{dst.name}.fractfs.tmp.{os.getpid()}")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        finally:
            if tmp.exists():
                tmp.unlink()


def make_backend(cfg) -> Backend:
    """Construct the backend for a :class:`~fractfs.config.Config`."""
    if cfg.backend == "mount":
        if cfg.remote_root is None:
            raise ValueError("backend 'mount' requires fractfs_REMOTE_ROOT to be set")
        return PosixBackend(cfg.remote_root)
    if cfg.backend == "fsspec":
        from .fsspec_backend import FsspecBackend

        return FsspecBackend(cfg)
    raise ValueError(f"no backend implementation for {cfg.backend!r}")
