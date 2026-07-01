"""Checkpoint / restore of LOCAL_SYNCED state, plus the background daemon.

The remote/durable store already holds VOLUME-tier data, so checkpointing only
concerns LOCAL_SYNCED files: the default local tree *and* ``local``-tier files
pinned inside a Volume dir (which physically live in scratch). LOCAL_IGNORED is
skipped everywhere.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .backend import Backend, make_backend
from .config import Config
from .resolver import Tier, resolve
from .resolver import _under_any_dir

STATE_FILENAME = ".fractfs-state.json"


def physical_local_path(cfg: Config, rel_path: str) -> Path:
    """Where a LOCAL_* file for ``rel_path`` physically lives on the node.

    Files inside a ``[dirs]`` directory are back-symlinked to scratch; everything
    else lives directly under the app root.
    """
    if _under_any_dir(rel_path, cfg.dir_paths):
        return cfg.scratch / rel_path
    return cfg.root / rel_path


class SyncEngine:
    """Stateless-ish engine that checkpoints and restores LOCAL_SYNCED files."""

    def __init__(self, cfg: Config, backend: Optional[Backend] = None):
        self.cfg = cfg
        self.backend = backend or make_backend(cfg)
        self._ckpt = cfg.checkpoint_subdir
        self.last_sync_time: Optional[float] = None
        # Files belonging to the deployed app bundle: re-supplied from the image
        # on every cold start, so never worth checkpointing. Populated by
        # detect_bundle() at init() when cfg.auto_ignore_bundle is on.
        self.bundle_paths: set = set()

    # -- bundle detection --------------------------------------------------

    def detect_bundle(self) -> set:
        """Identify deployed-bundle files so they're excluded from the checkpoint.

        The bundle is everything present in the local tree at startup that is
        *not* already known runtime state (i.e. not in the checkpoint manifest).
        On a cold ephemeral disk that's exactly the freshly-deployed app; on a
        warm/persistent disk the subtraction keeps real runtime files out of the
        bundle so they keep being checkpointed.

        Recomputed each ``init()`` so it tracks redeploys automatically. Must run
        before any checkpoint (and before/around restore — restored files are in
        the manifest, so they're never mistaken for bundle).
        """
        known_runtime = set(self._load_state().keys())
        present = {rel for rel, _abs in self._iter_candidates()}
        self.bundle_paths = present - known_runtime
        return self.bundle_paths

    # -- checkpoint --------------------------------------------------------

    def checkpoint(self) -> List[str]:
        """Copy changed LOCAL_SYNCED files to the checkpoint. Returns copied paths."""
        state = self._load_state()
        copied: List[str] = []
        for rel_path, abs_path in self._iter_candidates():
            if rel_path in self.bundle_paths:
                continue  # part of the deploy bundle; re-supplied on cold start
            sig = self._signature(abs_path)
            if state.get(rel_path) == sig:
                continue  # unchanged since last checkpoint
            remote = f"{self._ckpt}/{rel_path}"
            self.backend.put_file(abs_path, remote)
            state[rel_path] = sig
            copied.append(rel_path)
        self._save_state(state)
        self.last_sync_time = time.time()
        return copied

    # -- restore -----------------------------------------------------------

    def restore(self, *, overwrite: bool = False) -> List[str]:
        """Pull LOCAL_SYNCED files down from the checkpoint into their local home.

        By default only restores files that are missing locally (cold start),
        never clobbering newer local state. Returns restored rel paths.
        """
        restored: List[str] = []
        prefix = f"{self._ckpt}/"
        for remote in self.backend.list_files(self._ckpt):
            if not remote.startswith(prefix):
                continue
            rel_path = remote[len(prefix):]
            if not rel_path or rel_path == STATE_FILENAME:
                continue
            if resolve(rel_path, self.cfg) == Tier.LOCAL_IGNORED:
                continue
            dest = physical_local_path(self.cfg, rel_path)
            if dest.exists() and not overwrite:
                continue
            self.backend.get_file(f"{prefix}{rel_path}", dest)
            restored.append(rel_path)
        return restored

    # -- walking -----------------------------------------------------------

    def _iter_candidates(self) -> Iterator[Tuple[str, Path]]:
        """Yield (rel_path, abs_path) for every physically-local LOCAL_SYNCED file.

        This is the raw set *before* bundle exclusion (checkpoint applies that);
        detect_bundle() relies on seeing the full set.
        """
        # Pass A: the default local tree under root. Do not follow symlinks, so we
        # never descend the Volume dir links (their contents are remote).
        for abs_path in _walk_files(self.cfg.root, follow=False):
            rel = _rel(abs_path, self.cfg.root)
            if rel is None:
                continue
            if resolve(rel, self.cfg) == Tier.LOCAL_SYNCED and not _under_any_dir(rel, self.cfg.dir_paths):
                yield rel, abs_path

        # Pass B: back-symlinked local files inside Volume dirs live in scratch.
        if self.cfg.scratch.exists():
            for abs_path in _walk_files(self.cfg.scratch, follow=False):
                rel = _rel(abs_path, self.cfg.scratch)
                if rel is None:
                    continue
                if resolve(rel, self.cfg) == Tier.LOCAL_SYNCED:
                    yield rel, abs_path

    # -- change detection --------------------------------------------------

    def _signature(self, path: Path) -> str:
        st = path.stat()
        if self.cfg.use_content_hash:
            return f"h:{_hash_file(path)}"
        return f"{st.st_size}:{int(st.st_mtime)}"

    # -- state file --------------------------------------------------------
    #
    # The change-detection manifest lives on the durable store next to the
    # checkpoint so subsequent cold starts don't re-copy everything. It is moved
    # through a local scratch temp file so this works for any backend (POSIX or
    # fsspec), not just a mounted Volume.

    def _state_remote(self) -> str:
        return f"{self._ckpt}/{STATE_FILENAME}"

    def _state_tmp(self) -> Path:
        self.cfg.scratch.mkdir(parents=True, exist_ok=True)
        return self.cfg.scratch / f".{STATE_FILENAME}.{os.getpid()}"

    def _load_state(self) -> Dict[str, str]:
        try:
            if self.backend.exists(self._state_remote()):
                tmp = self._state_tmp()
                self.backend.get_file(self._state_remote(), tmp)
                data = json.loads(tmp.read_text())
                tmp.unlink(missing_ok=True)
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            pass
        return {}

    def _save_state(self, state: Dict[str, str]) -> None:
        self.backend.makedirs(self._ckpt)
        tmp = self._state_tmp()
        tmp.write_text(json.dumps(state, indent=0, sort_keys=True))
        self.backend.put_file(tmp, self._state_remote())
        tmp.unlink(missing_ok=True)


class SyncDaemon:
    """Runs :meth:`SyncEngine.checkpoint` on a fixed interval in a daemon thread."""

    def __init__(self, engine: SyncEngine, interval: int):
        self.engine = engine
        self.interval = max(1, int(interval))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="fractfs-sync", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.engine.checkpoint()
            except Exception:  # never let the daemon die on a transient error
                import logging

                logging.getLogger("fractfs").exception("checkpoint failed")

    def stop(self, *, final_sync: bool = True) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 5)
            self._thread = None
        if final_sync:
            self.engine.checkpoint()


def _walk_files(base: Path, *, follow: bool) -> Iterator[Path]:
    if not base.exists():
        return
    for dirpath, _dirnames, filenames in os.walk(base, followlinks=follow):
        for name in filenames:
            yield Path(dirpath) / name


def _rel(abs_path: Path, base: Path) -> Optional[str]:
    try:
        return abs_path.relative_to(base).as_posix()
    except ValueError:
        return None


def _hash_file(path: Path) -> str:
    try:
        import xxhash

        h = xxhash.xxh3_64()
    except ImportError:
        import hashlib

        h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
