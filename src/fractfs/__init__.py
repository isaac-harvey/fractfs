"""fractfs — drop-in tiered file storage for apps on ephemeral nodes.

The whole drop-in surface is three calls::

    import fractfs
    fractfs.init()        # load config, provision symlinks, restore, start syncing
    fractfs.sync_now()    # optional: force a checkpoint (e.g. before shutdown)
    fractfs.status()      # optional: inspect tiers and last sync time
"""

from __future__ import annotations

import atexit
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .backend import make_backend
from .config import Config, load_config
from .provisioner import ClobberError, provision, warnings_for
from .resolver import Tier, resolve
from .sync import SyncDaemon, SyncEngine

__all__ = [
    "init",
    "sync_now",
    "status",
    "shutdown",
    "resolve",
    "Tier",
    "Config",
    "load_config",
    "ClobberError",
]

__version__ = "0.2.1"

log = logging.getLogger("fractfs")


@dataclass
class _Runtime:
    cfg: Config
    engine: SyncEngine
    daemon: Optional[SyncDaemon] = None


_RUNTIME: Optional[_Runtime] = None


def init(
    root: Optional[os.PathLike] = None,
    *,
    force: bool = False,
    start_daemon: bool = True,
    restore: bool = True,
) -> Config:
    """Initialise fractfs: load config, provision symlinks, restore, start syncing.

    Blocks on restore before returning so the app never reads cold state. Safe to
    call once at startup. ``force=True`` lets provisioning migrate a non-empty real
    local dir into the remote store (see :class:`ClobberError`).
    """
    global _RUNTIME

    cfg = load_config(root)

    # [dirs] redirect is symlink-based and needs a POSIX target. On an object-store
    # (fsspec) backend there are no symlinks, and REMOTE-tier files are never
    # checkpointed — so a [dirs] file would be stranded on ephemeral disk and lost.
    # Refuse loudly rather than silently drop data.
    if cfg.dir_paths and cfg.has_remote_store() and not cfg.supports_redirect():
        raise ValueError(
            f"[dirs].paths redirect is not supported by the {cfg.backend!r} backend: "
            "directory symlinks need a POSIX path. FUSE-mount the object store "
            "(mountpoint-s3 / gcsfuse / blobfuse2) and use backend='mount', or remove "
            "[dirs].paths and use fractfs only for checkpoint/restore of local state."
        )

    engine = SyncEngine(cfg, backend=make_backend(cfg)) if cfg.has_remote_store() else None

    for warning in warnings_for(cfg):
        log.warning("fractfs config: %s", warning)

    if cfg.has_remote_store():
        if cfg.supports_redirect():
            actions = provision(cfg, force=force)
            for a in actions:
                log.debug("provision: %s", a)
        # Identify the deployed bundle before restore so it's excluded from the
        # checkpoint (re-supplied from the image on every cold start anyway).
        if cfg.auto_ignore_bundle and engine is not None:
            bundle = engine.detect_bundle()
            if bundle:
                log.info("fractfs auto-ignoring %d deploy-bundle file(s)", len(bundle))
        # Cold-start ordering: restore must finish before the app reads anything.
        if restore and engine is not None:
            restored = engine.restore()
            if restored:
                log.info("fractfs restored %d checkpointed file(s)", len(restored))
    else:
        log.warning(
            "fractfs: no fractfs_REMOTE_ROOT set; running in passthrough mode "
            "(no redirect, no checkpoint)."
        )

    daemon = None
    if engine is not None and start_daemon and cfg.sync_interval > 0:
        daemon = SyncDaemon(engine, cfg.sync_interval)
        daemon.start()
        atexit.register(_atexit_stop)

    _RUNTIME = _Runtime(cfg=cfg, engine=engine, daemon=daemon) if engine is not None else _Runtime(
        cfg=cfg, engine=_NullEngine(cfg)  # type: ignore[arg-type]
    )
    return cfg


def sync_now() -> List[str]:
    """Force a checkpoint immediately. Returns the rel paths that were copied."""
    rt = _require_runtime()
    return rt.engine.checkpoint()


def status() -> Dict[str, Any]:
    """Report current configuration, the tier of each tracked path, and last sync."""
    rt = _require_runtime()
    cfg = rt.cfg
    tracked: Dict[str, str] = {}
    for d in cfg.dir_paths:
        tracked[d] = resolve(d, cfg).value
    return {
        "backend": cfg.backend,
        "remote_root": str(cfg.remote_root) if cfg.remote_root else None,
        "scratch": str(cfg.scratch),
        "sync_interval": cfg.sync_interval,
        "has_remote_store": cfg.has_remote_store(),
        "supports_redirect": cfg.supports_redirect(),
        "daemon_running": rt.daemon is not None,
        "last_sync_time": getattr(rt.engine, "last_sync_time", None),
        "auto_ignore_bundle": cfg.auto_ignore_bundle,
        "bundle_file_count": len(getattr(rt.engine, "bundle_paths", set())),
        "dirs": tracked,
        "ignore_patterns": list(cfg.ignore_patterns),
        "local_patterns": list(cfg.local_patterns),
        "warnings": warnings_for(cfg),
    }


def shutdown(*, final_sync: bool = True) -> None:
    """Stop the sync daemon, optionally running one last checkpoint."""
    global _RUNTIME
    if _RUNTIME is not None and _RUNTIME.daemon is not None:
        _RUNTIME.daemon.stop(final_sync=final_sync)
        _RUNTIME.daemon = None


def _require_runtime() -> _Runtime:
    if _RUNTIME is None:
        raise RuntimeError("fractfs.init() has not been called")
    return _RUNTIME


def _atexit_stop() -> None:
    try:
        shutdown(final_sync=True)
    except Exception:  # best-effort on interpreter shutdown
        pass


class _NullEngine:
    """Stand-in when there is no remote store: checkpoint/restore are no-ops."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_sync_time = None
        self.bundle_paths: set = set()

    def detect_bundle(self) -> set:
        return set()

    def checkpoint(self) -> List[str]:
        return []

    def restore(self, **_: Any) -> List[str]:
        return []
