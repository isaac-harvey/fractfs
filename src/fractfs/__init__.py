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

__version__ = "0.1.0"

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
    local dir into the Volume (see :class:`ClobberError`).
    """
    global _RUNTIME

    cfg = load_config(root)
    engine = SyncEngine(cfg, backend=make_backend(cfg)) if cfg.is_provisionable() else None

    for warning in warnings_for(cfg):
        log.warning("fractfs config: %s", warning)

    if cfg.is_provisionable():
        actions = provision(cfg, force=force)
        for a in actions:
            log.debug("provision: %s", a)
        # Cold-start ordering: restore must finish before the app reads anything.
        if restore and engine is not None:
            restored = engine.restore()
            if restored:
                log.info("fractfs restored %d checkpointed file(s)", len(restored))
    else:
        log.warning(
            "fractfs: no fractfs_VOLUME_ROOT set; running in passthrough mode "
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
        "volume_root": str(cfg.volume_root) if cfg.volume_root else None,
        "scratch": str(cfg.scratch),
        "sync_interval": cfg.sync_interval,
        "provisionable": cfg.is_provisionable(),
        "daemon_running": rt.daemon is not None,
        "last_sync_time": getattr(rt.engine, "last_sync_time", None),
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
    """Stand-in when there is no Volume: checkpoint/restore are no-ops."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.last_sync_time = None

    def checkpoint(self) -> List[str]:
        return []

    def restore(self, **_: Any) -> List[str]:
        return []
