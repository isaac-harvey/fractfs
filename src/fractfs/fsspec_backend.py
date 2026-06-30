"""fsspec-backed implementation (S3 / ADLS / GCS) — optional extra.

Kept in its own module so importing fractfs never pulls in ``fsspec``/``s3fs``
unless an fsspec backend is actually requested. Install with ``fractfs[s3]``.
"""

from __future__ import annotations

import os
from typing import Iterable


class FsspecBackend:
    """Backend over any fsspec filesystem rooted at ``cfg.volume_root`` URL.

    ``volume_root`` here is an fsspec URL such as ``s3://bucket/prefix``.
    """

    def __init__(self, cfg):
        try:
            import fsspec
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "the s3/fsspec backend requires the 'fsspec' extra: pip install 'fractfs[s3]'"
            ) from exc
        if cfg.volume_root is None:
            raise ValueError("fsspec backend requires fractfs_VOLUME_ROOT (an fsspec URL)")
        self.url_root = str(cfg.volume_root).rstrip("/")
        self.fs, self.path_root = fsspec.core.url_to_fs(self.url_root)

    def _abs(self, path: str) -> str:
        path = str(path)
        if path.startswith(self.path_root):
            return path
        return f"{self.path_root}/{path.lstrip('/')}"

    def exists(self, path: str) -> bool:
        return self.fs.exists(self._abs(path))

    def makedirs(self, path: str) -> None:
        self.fs.makedirs(self._abs(path), exist_ok=True)

    def put_file(self, local_path: os.PathLike, remote_path: str) -> None:
        self.fs.put_file(str(local_path), self._abs(remote_path))

    def get_file(self, remote_path: str, local_path: os.PathLike) -> None:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        self.fs.get_file(self._abs(remote_path), str(local_path))

    def list_files(self, path: str) -> Iterable[str]:
        base = self._abs(path)
        if not self.fs.exists(base):
            return
        for full in self.fs.find(base):
            rel = full[len(self.path_root):].lstrip("/")
            yield rel

    def remove(self, path: str) -> None:
        target = self._abs(path)
        if self.fs.exists(target):
            self.fs.rm(target, recursive=True)
