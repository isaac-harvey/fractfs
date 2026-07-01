"""Static analysis of gitignore patterns, used to decide what can be pinned.

A ``local``/``ignore`` file inside a remote-redirected ``[dirs]`` directory only
stays node-local if a back-symlink exists *before* the write. We can pre-create
that symlink for any pattern naming a concrete location; we cannot for a glob,
because the filename isn't known until the app creates it.
"""

from __future__ import annotations

from typing import List, Tuple

from .resolver import _under_any_dir

# gitignore wildcard metacharacters. A pattern containing any of these names a
# set of paths, not one path, so its back-symlink location can't be predicted.
_GLOB_META = "*?["


def is_glob(pattern: str) -> bool:
    return any(c in pattern for c in _GLOB_META)


def is_negation(pattern: str) -> bool:
    return pattern.lstrip().startswith("!")


def is_dir_pattern(pattern: str) -> bool:
    """True if the pattern targets a directory (trailing slash)."""
    return pattern.rstrip().endswith("/")


def is_anchored(pattern: str) -> bool:
    """gitignore anchoring: a leading or internal slash pins the pattern to root."""
    body = pattern.strip()
    if body.startswith("/"):
        return True
    return "/" in body.rstrip("/")


def is_concrete(pattern: str) -> bool:
    """A pattern we can pre-create a back-symlink for (an exact path/name)."""
    return bool(pattern.strip()) and not is_negation(pattern) and not is_glob(pattern)


def pin_targets(pattern: str, dir_paths: List[str]) -> List[Tuple[str, bool]]:
    """Back-symlink targets for ``pattern`` that fall inside ``dir_paths``.

    Returns ``(rel_path, is_dir)`` tuples. Empty for globs/negations (can't be
    predicted) and for concrete paths that don't land inside any remote dir
    (those already live in the plain local tree and need no symlink).
    """
    if not is_concrete(pattern):
        return []
    is_dir = is_dir_pattern(pattern)
    clean = pattern.strip().strip("/")
    if not clean:
        return []
    if is_anchored(pattern):
        return [(clean, is_dir)] if _under_any_dir(clean, dir_paths) else []
    # Unanchored bare name (e.g. "manifest.json", ".locks/"): the common case is
    # one at the top of each remote dir — reserve a slot there.
    return [(f"{d}/{clean}", is_dir) for d in dir_paths]
