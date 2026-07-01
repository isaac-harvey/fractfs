# Tiers in depth: the `local` tier and keeping files node-local

See the [README](../README.md) for the three-tier overview and precedence rules.
This document covers the two things that need more than a table: what the `local`
tier guarantees, and how fractfs keeps specific files on node-local disk even
when they live inside a remote-redirected `[dirs]` directory.

## The `local` tier — what it is and is not

`local` is for small files co-written with large blobs (a `manifest.json` next to
`x.parquet`) that must survive restart but shouldn't pay the cost of going
direct-to-remote. Local disk gives proper atomic `rename`; a FUSE-mounted remote
store (e.g. a Databricks Volume) has real per-op overhead and weak atomicity, so
for small mutable metadata local-tier is both faster and safer than writing it
straight to the remote store.

**The limit:** `local` does **not** give blob↔metadata transactional
consistency. On cold restart you restore a checkpoint up to
`FRACTFS_SYNC_INTERVAL` seconds stale, while the blob on the remote store is
current. Use `local` only for independent / rebuildable small state. If the small
file is a pointer into the blob that must be exactly consistent, either put it in
a `[dirs]` directory too (shared fate, accept the remote-write cost) or make it
reconstructable from the blob on startup.

## Keeping `local` files always-local (lock files, sqlite, manifests)

A `[dirs]` directory is a symlink to the remote store, so files created inside it
follow that link to the remote store *by default*. To keep a `local`/`ignore`
file on fast node-local disk instead, fractfs places a **back-symlink** on the
remote store that points at node-local scratch:

```
REMOTE/data/blobs/manifest.json  ->  $FRACTFS_SCRATCH/data/blobs/manifest.json
```

A symlink has to exist before the write to redirect it, so what fractfs can pin
depends on whether it can predict the path:

- **Exact filenames** (`manifest.json`, `index.sqlite`, or anchored
  `data/blobs/manifest.json`) are pinned with a back-symlink **pre-created at
  `init()`**, possibly dangling — so the file is local from its **very first
  write**, no restart needed.
- **Directory patterns** (a pattern ending in `/`, e.g. `.locks/`) pin a whole
  subtree. *Any* filename created inside it lands local — this is the escape
  hatch for lock files and other state with unpredictable names. Point the app at
  a pinned subdirectory:

  ```toml
  [ignore]
  patterns = [".locks/"]    # data/blobs/.locks/<anything> stays node-local
  ```

- **Globs** (`*.lock`, `*.tmp`) **cannot** be pre-pinned — fractfs can't know the
  filename until the app creates it, and intercepting the write would mean
  monkeypatching I/O (explicitly rejected). A `*.lock` created *directly* inside a
  `[dirs]` dir therefore lands on the remote store. `init()` emits a warning (also
  in `status()["warnings"]`) when a `[local]` glob could be affected.

Anything that spawns sidecar files with names
you don't control *next to* the data — a SQLite db emitting `-wal`/`-shm`, a
library dropping `<name>.lock` beside the file it locks — should **not** live
loose inside a `[dirs]` directory. Put that state in the default local tree (not
under any `[dirs]` path) where it's plain local disk + checkpointed with all
sidecars co-located, or confine it to a pinned `foo/` subdirectory.
