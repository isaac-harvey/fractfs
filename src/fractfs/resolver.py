"""The resolver: the single source of truth for which tier a path belongs to.

Both the provisioner and the sync walker call into here so the spec stays
executable rather than re-implemented in two places.
"""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


class Tier(Enum):
    """Where a path physically lives and whether it is checkpointed."""

    VOLUME = "volume"  # redirected to the remote store; not checkpointed (already durable)
    LOCAL_SYNCED = "local_synced"  # local disk, included in the checkpoint
    LOCAL_IGNORED = "local_ignored"  # local disk, never checkpointed


def resolve(rel_path: str, cfg: "Config") -> Tier:
    """Map a repo-relative POSIX path to its tier.

    Precedence (highest first): ignore > local > dirs > default. ``local`` and
    ``ignore`` exist precisely to pull specific small files *out* of the Volume
    redirect, so they must win over ``dirs``.
    """
    rel = _normalize(rel_path)
    if cfg.ignore_spec.match_file(rel):
        return Tier.LOCAL_IGNORED
    if cfg.local_spec.match_file(rel):
        return Tier.LOCAL_SYNCED
    if _under_any_dir(rel, cfg.dir_paths):
        return Tier.VOLUME
    return Tier.LOCAL_SYNCED  # default: local + checkpointed


def _normalize(rel_path: str) -> str:
    """Normalize to a forward-slash relative path with no leading ``./`` or ``/``."""
    p = PurePosixPath(str(rel_path).replace("\\", "/"))
    if p.is_absolute():
        p = p.relative_to(p.anchor)
    return str(p)


def _under_any_dir(rel_path: str, dir_paths) -> bool:
    """True if ``rel_path`` is one of the dirs or nested under one of them."""
    p = PurePosixPath(rel_path)
    parents = {str(x) for x in p.parents}
    return any(d == str(p) or d in parents for d in dir_paths)
