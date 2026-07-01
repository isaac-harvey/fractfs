"""Config loader: parse ``.fractfs.toml`` + environment into a ``Config``.

Env vars override the TOML file for the scalar fields so deployments can tune
behaviour (backend, remote root, cadence) without editing the repo.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

import pathspec

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on <3.11
    import tomli as tomllib

CONFIG_FILENAME = ".fractfs.toml"

# Backends we know how to provision against, named by *mechanism*:
#   mount  — the remote store is reachable as a POSIX filesystem path
#            (a Databricks Volume mount, NFS/EFS, SMB, or a plain local dir).
#   fsspec — the remote store is an fsspec URL (S3, GCS, ADLS, ...).
BACKENDS = ("mount", "fsspec")

# Friendly aliases normalised to a canonical backend. ``s3`` is by far the most
# common fsspec target, so it stays as an on-ramp even though the impl is generic.
_BACKEND_ALIASES = {"s3": "fsspec"}

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
    backend: str = "mount"
    # A mount backend stores a filesystem ``Path``; an fsspec backend stores the
    # store's URL (``s3://…``) as a ``str`` — see __post_init__ for why it must not
    # be run through pathlib.
    remote_root: Optional[Union[Path, str]] = None
    scratch: Path = Path(_DEFAULT_SCRATCH)
    sync_interval: int = _DEFAULT_SYNC_INTERVAL
    checkpoint_subdir: str = _DEFAULT_CHECKPOINT_SUBDIR
    dir_paths: List[str] = field(default_factory=list)
    ignore_patterns: List[str] = field(default_factory=list)
    local_patterns: List[str] = field(default_factory=list)
    use_content_hash: bool = False
    auto_ignore_bundle: bool = True

    ignore_spec: pathspec.PathSpec = field(init=False)
    local_spec: pathspec.PathSpec = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.scratch = Path(self.scratch)
        self.backend = _BACKEND_ALIASES.get(self.backend, self.backend)
        if self.backend not in BACKENDS:
            raise ValueError(
                f"unknown fractfs backend {self.backend!r}; expected one of {BACKENDS} "
                f"(aliases: {', '.join(_BACKEND_ALIASES)})"
            )
        if self.remote_root is not None:
            # The mount backend addresses the store as a filesystem path. The fsspec
            # backend addresses it as a URL (s3://, gs://, abfs://) which must NOT go
            # through pathlib: Path("s3://b/x") collapses the "//" to "s3:/b/x", which
            # fsspec then resolves as a *local* file path — silently writing to disk
            # instead of the object store. So keep URLs as plain strings.
            if self.backend == "mount":
                self.remote_root = Path(self.remote_root)
            else:
                self.remote_root = str(self.remote_root)
        # Normalize dir paths to forward-slash relative strings (drop "./", trailing "/").
        self.dir_paths = [d.strip("/").replace("\\", "/") for d in self.dir_paths if d.strip("/")]
        self.ignore_spec = pathspec.PathSpec.from_lines(_PATHSPEC_FACTORY, self.ignore_patterns)
        self.local_spec = pathspec.PathSpec.from_lines(_PATHSPEC_FACTORY, self.local_patterns)

    # -- derived paths -----------------------------------------------------

    @property
    def checkpoint_root(self) -> Optional[Path]:
        """Absolute path under the remote store where LOCAL_SYNCED checkpoints land.

        Only meaningful for a filesystem (``mount``) root; fsspec backends address
        checkpoints by URL string, not this path, so this returns ``None`` there.
        """
        if not isinstance(self.remote_root, Path):
            return None
        return self.remote_root / self.checkpoint_subdir

    def has_remote_store(self) -> bool:
        """Whether a durable store is configured at all.

        Gates checkpoint/restore (available on every backend). Without a remote
        root fractfs runs in passthrough mode (no redirect, no checkpoint).
        """
        return self.remote_root is not None

    def supports_redirect(self) -> bool:
        """Whether ``[dirs]`` big-file redirect can run.

        Redirect is implemented with directory symlinks, which need a POSIX target,
        so only the ``mount`` backend qualifies. An object store (fsspec) has no
        symlinks — FUSE-mount it (mountpoint-s3/gcsfuse/blobfuse2) and use the
        ``mount`` backend for redirect, or use fsspec for checkpoint/restore only.
        """
        return self.backend == "mount" and self.remote_root is not None


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

    backend = _env("BACKEND") or data.get("backend") or "mount"

    remote_root = _env("REMOTE_ROOT") or data.get("remote_root")
    scratch = _env("SCRATCH") or data.get("scratch") or _DEFAULT_SCRATCH
    checkpoint_subdir = (
        _env("CHECKPOINT_SUBDIR") or data.get("checkpoint_subdir") or _DEFAULT_CHECKPOINT_SUBDIR
    )

    sync_interval_raw = _env("SYNC_INTERVAL") or data.get("sync_interval")
    sync_interval = int(sync_interval_raw) if sync_interval_raw is not None else _DEFAULT_SYNC_INTERVAL

    hash_raw = _env("CONTENT_HASH") or data.get("content_hash")
    use_content_hash = _as_bool(hash_raw, default=False)

    bundle_raw = _env("AUTO_IGNORE_BUNDLE")
    if bundle_raw is None and "auto_ignore_bundle" in data:
        bundle_raw = data.get("auto_ignore_bundle")
    auto_ignore_bundle = _as_bool(bundle_raw, default=True)

    return Config(
        root=root,
        backend=backend,
        remote_root=remote_root,
        scratch=scratch,
        sync_interval=sync_interval,
        checkpoint_subdir=checkpoint_subdir,
        dir_paths=list(dirs),
        ignore_patterns=list(ignore),
        local_patterns=list(local),
        use_content_hash=use_content_hash,
        auto_ignore_bundle=auto_ignore_bundle,
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
