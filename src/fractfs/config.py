"""Config loader: parse ``.fractfs.toml`` + environment into a ``Config``.

Env vars override the TOML file for the scalar fields so deployments can tune
behaviour (backend, volume root, cadence) without editing the repo.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import pathspec

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on <3.11
    import tomli as tomllib

CONFIG_FILENAME = ".fractfs.toml"

# Backends we know how to provision against.
BACKENDS = ("volumes", "s3", "local")

# pathspec renamed the gitignore factory; prefer the current name, fall back for
# older pathspec releases that only ship "gitwildmatch".
try:
    pathspec.PathSpec.from_lines("gitignore", [])
    _PATHSPEC_FACTORY = "gitignore"
except (ValueError, KeyError, LookupError):  # pragma: no cover - old pathspec
    _PATHSPEC_FACTORY = "gitwildmatch"

_DEFAULT_SYNC_INTERVAL = 300
_DEFAULT_CHECKPOINT_SUBDIR = "_checkpoint"
_DEFAULT_SCRATCH = "/tmp/fractfs"


@dataclass
class Config:
    """Resolved configuration.

    ``root`` is the application root (the dir holding ``.fractfs.toml``); all
    ``dir_paths`` and resolver inputs are relative to it.
    """

    root: Path
    backend: str = "local"
    volume_root: Optional[Path] = None
    scratch: Path = Path(_DEFAULT_SCRATCH)
    sync_interval: int = _DEFAULT_SYNC_INTERVAL
    checkpoint_subdir: str = _DEFAULT_CHECKPOINT_SUBDIR
    dir_paths: List[str] = field(default_factory=list)
    ignore_patterns: List[str] = field(default_factory=list)
    local_patterns: List[str] = field(default_factory=list)
    use_content_hash: bool = False

    ignore_spec: pathspec.PathSpec = field(init=False)
    local_spec: pathspec.PathSpec = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if self.volume_root is not None:
            self.volume_root = Path(self.volume_root)
        self.scratch = Path(self.scratch)
        # Normalize dir paths to forward-slash relative strings (drop "./", trailing "/").
        self.dir_paths = [d.strip("/").replace("\\", "/") for d in self.dir_paths if d.strip("/")]
        self.ignore_spec = pathspec.PathSpec.from_lines(_PATHSPEC_FACTORY, self.ignore_patterns)
        self.local_spec = pathspec.PathSpec.from_lines(_PATHSPEC_FACTORY, self.local_patterns)
        if self.backend not in BACKENDS:
            raise ValueError(
                f"unknown fractfs backend {self.backend!r}; expected one of {BACKENDS}"
            )

    # -- derived paths -----------------------------------------------------

    @property
    def checkpoint_root(self) -> Optional[Path]:
        """Absolute path under the Volume where LOCAL_SYNCED checkpoints land."""
        if self.volume_root is None:
            return None
        return self.volume_root / self.checkpoint_subdir

    def is_provisionable(self) -> bool:
        """Whether dir-redirect / back-symlink provisioning can run.

        Requires a Volume root; without one (pure ``local`` backend, no mount)
        only checkpoint/restore against a local volume_root is meaningful.
        """
        return self.volume_root is not None


def _env(name: str) -> Optional[str]:
    return os.environ.get(f"fractfs_{name}") or os.environ.get(f"FRACTFS_{name}")


def load_config(root: Optional[os.PathLike] = None) -> Config:
    """Load config from ``<root>/.fractfs.toml`` with env-var overrides.

    ``root`` defaults to ``$fractfs_ROOT`` then the current working directory.
    """
    if root is None:
        root = _env("ROOT") or os.getcwd()
    root = Path(root).resolve()

    data = _read_toml(root / CONFIG_FILENAME)

    dirs = data.get("dirs", {}).get("paths", []) or []
    ignore = data.get("ignore", {}).get("patterns", []) or []
    local = data.get("local", {}).get("patterns", []) or []

    backend = _env("BACKEND") or data.get("backend") or "local"

    volume_root = _env("VOLUME_ROOT") or data.get("volume_root")
    scratch = _env("SCRATCH") or data.get("scratch") or _DEFAULT_SCRATCH
    checkpoint_subdir = (
        _env("CHECKPOINT_SUBDIR") or data.get("checkpoint_subdir") or _DEFAULT_CHECKPOINT_SUBDIR
    )

    sync_interval_raw = _env("SYNC_INTERVAL") or data.get("sync_interval")
    sync_interval = int(sync_interval_raw) if sync_interval_raw is not None else _DEFAULT_SYNC_INTERVAL

    hash_raw = _env("CONTENT_HASH") or data.get("content_hash")
    use_content_hash = _as_bool(hash_raw, default=False)

    return Config(
        root=root,
        backend=backend,
        volume_root=volume_root,
        scratch=scratch,
        sync_interval=sync_interval,
        checkpoint_subdir=checkpoint_subdir,
        dir_paths=list(dirs),
        ignore_patterns=list(ignore),
        local_patterns=list(local),
        use_content_hash=use_content_hash,
    )


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
