"""Idempotent provisioning: lay down the symlinks that implement the tiers.

Run on every ``init()``. Converges to the same layout each time:

* Each ``[dirs].paths`` directory becomes a symlink into the remote store, so its
  contents (including files created later) land remote by default.
* Each concrete ``local``/``ignore`` pattern that names a location inside one of
  those dirs is pre-pinned with a *back-symlink* on the remote store pointing at
  node-local scratch — created up front (possibly dangling) so the very first
  write lands local. File patterns pin a file; directory patterns (``foo/``) pin
  a whole subtree, so arbitrarily-named files inside it (lock files, etc.) are
  always local.
* Any pre-existing file under a dir that resolves to a LOCAL tier is pinned too.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from .config import Config
from .patterns import is_glob, is_negation, pin_targets
from .resolver import Tier, resolve


class ClobberError(RuntimeError):
    """Raised when provisioning would replace real local data with a symlink."""


def provision(cfg: Config, *, force: bool = False) -> List[str]:
    """Provision all configured dirs and back-symlinks. Returns an action log.

    ``force=True`` permits migrating a non-empty real local directory into the
    remote store before replacing it with a symlink (otherwise that case raises
    :class:`ClobberError` to avoid eating real data on a misconfigured run).
    """
    if not cfg.supports_redirect():
        raise ValueError(
            "provisioning requires the 'mount' backend with fractfs_REMOTE_ROOT set; "
            f"backend {cfg.backend!r} cannot lay down directory symlinks"
        )

    actions: List[str] = []
    cfg.scratch.mkdir(parents=True, exist_ok=True)

    for rel_dir in cfg.dir_paths:
        actions.extend(_provision_dir(cfg, rel_dir, force=force))
    return actions


def warnings_for(cfg: Config) -> List[str]:
    """Config issues that can't be auto-fixed and need the user's attention.

    The key one: a ``[local]`` *glob* can't be guaranteed local inside a remote
    dir, because a brand-new arbitrary filename created there follows the dir
    symlink to the remote store before fractfs ever sees it.
    """
    out: List[str] = []
    if not cfg.dir_paths:
        return out
    for pat in cfg.local_patterns:
        if is_negation(pat) or not is_glob(pat):
            continue
        out.append(
            f"[local] pattern {pat!r} is a glob: files it matches that are created "
            f"directly inside a [dirs] directory after init() will land on the remote store, "
            f"not on local disk. Pin them with an exact filename, a subdirectory "
            f"pattern (e.g. '.locks/'), or keep such files out of [dirs] dirs."
        )
    return out


# -- per-dir provisioning -------------------------------------------------


def _provision_dir(cfg: Config, rel_dir: str, *, force: bool) -> List[str]:
    actions: List[str] = []
    assert cfg.remote_root is not None
    local_dir = cfg.root / rel_dir
    remote_dir = cfg.remote_root / rel_dir

    remote_dir.mkdir(parents=True, exist_ok=True)

    # 1. The dir itself becomes a symlink to the remote store.
    if local_dir.is_symlink():
        if _resolves_to(local_dir, remote_dir):
            actions.append(f"ok       {rel_dir} -> {remote_dir} (already linked)")
        else:
            local_dir.unlink()
            _symlink(local_dir, remote_dir)
            actions.append(f"relink   {rel_dir} -> {remote_dir}")
    elif local_dir.exists():
        if not local_dir.is_dir():
            raise ClobberError(f"{local_dir} exists and is not a directory; refusing to replace")
        contents = list(local_dir.iterdir())
        if contents and not force:
            raise ClobberError(
                f"{local_dir} is a non-empty real directory; refusing to replace with a "
                f"symlink. Re-run with force=True to migrate its contents to {remote_dir}."
            )
        if contents:
            _migrate_children(local_dir, remote_dir, actions, verb="migrate")
        local_dir.rmdir()
        _symlink(local_dir, remote_dir)
        actions.append(f"link     {rel_dir} -> {remote_dir}")
    else:
        local_dir.parent.mkdir(parents=True, exist_ok=True)
        _symlink(local_dir, remote_dir)
        actions.append(f"link     {rel_dir} -> {remote_dir}")

    # 2. Pre-pin concrete local/ignore patterns inside this dir (before the walk,
    #    so directory pins turn subdirs into symlinks the walk won't descend).
    actions.extend(_provision_pattern_pins(cfg, rel_dir))

    # 3. Pin any pre-existing files that resolve to a LOCAL tier.
    actions.extend(_provision_existing_files(cfg, rel_dir, remote_dir))
    return actions


def _provision_pattern_pins(cfg: Config, rel_dir: str) -> List[str]:
    """Create back-symlinks for every concrete local/ignore pattern under ``rel_dir``."""
    actions: List[str] = []
    seen = set()
    for pat in list(cfg.local_patterns) + list(cfg.ignore_patterns):
        for rel_path, is_dir in pin_targets(pat, [rel_dir]):
            if rel_path in seen:
                continue
            seen.add(rel_path)
            actions.append(_pin_back(cfg, rel_path, is_dir=is_dir))
    return actions


def _provision_existing_files(cfg: Config, rel_dir: str, remote_dir: Path) -> List[str]:
    """Pin pre-existing files on the remote store that resolve to a LOCAL tier.

    ``os.walk`` does not follow symlinks, so directory pins created in step 2 are
    not descended, and already-pinned files (symlinks) are skipped.
    """
    actions: List[str] = []
    assert cfg.remote_root is not None
    for dirpath, _dirs, files in os.walk(remote_dir):
        for name in files:
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            rel_path = str(full.relative_to(cfg.remote_root))
            if resolve(rel_path, cfg) in (Tier.LOCAL_SYNCED, Tier.LOCAL_IGNORED):
                actions.append(_pin_back(cfg, rel_path, is_dir=False))
    return actions


# -- back-symlink primitive -----------------------------------------------


def _pin_back(cfg: Config, rel_path: str, *, is_dir: bool) -> str:
    """Replace ``<remote>/<rel_path>`` with a symlink to node-local scratch.

    Works whether or not the file/dir exists yet: a missing target yields a
    (initially dangling) back-symlink so the first write lands local. Any real
    bytes already on the remote store are moved down to scratch first so nothing
    is lost. Idempotent: an existing correct link is left alone.
    """
    assert cfg.remote_root is not None
    remote_path = cfg.remote_root / rel_path
    scratch_target = cfg.scratch / rel_path

    remote_path.parent.mkdir(parents=True, exist_ok=True)
    if is_dir:
        scratch_target.mkdir(parents=True, exist_ok=True)
    else:
        scratch_target.parent.mkdir(parents=True, exist_ok=True)

    if remote_path.is_symlink():
        if _resolves_to(remote_path, scratch_target):
            return f"ok       {rel_path} pinned local (already)"
        remote_path.unlink()
    elif remote_path.exists():
        # Real bytes/contents already on the remote store — migrate down to scratch.
        if is_dir:
            _migrate_children(remote_path, scratch_target, _DISCARD)
            remote_path.rmdir()
        elif not scratch_target.exists():
            os.replace(remote_path, scratch_target)
        else:
            remote_path.unlink()

    _symlink(remote_path, scratch_target)
    kind = "dir " if is_dir else "file"
    return f"pin      {rel_path} -> {scratch_target} (node-local {kind})"


# -- helpers --------------------------------------------------------------

_DISCARD: List[str] = []  # sentinel sink for _migrate_children action logs


def _migrate_children(src_dir: Path, dst_dir: Path, actions: List[str], *, verb: str = "move") -> None:
    """Move children of ``src_dir`` into ``dst_dir`` (skipping name collisions)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        dst = dst_dir / item.name
        if dst.exists():
            if actions is not _DISCARD:
                actions.append(f"skip     {item} (already present at destination)")
            continue
        os.replace(item, dst)
        if actions is not _DISCARD:
            actions.append(f"{verb:8s} {item.name} -> {dst}")


def _symlink(link: Path, target: Path) -> None:
    link.symlink_to(target)


def _resolves_to(link: Path, target: Path) -> bool:
    try:
        return os.path.realpath(link) == os.path.realpath(target)
    except OSError:
        return False
