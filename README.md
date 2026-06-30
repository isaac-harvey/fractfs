# fractfs

**Drop-in tiered file storage for apps on ephemeral nodes.**

`fractfs` lets an app with limited, ephemeral local disk transparently push large
files to durable remote storage (Databricks Volumes primarily; S3 / any `fsspec`
backend by extension) while keeping small hot state local, with periodic
checkpoint/restore so a node stop/start doesn't lose data.

The only change to your application is one line at startup:

```python
import fractfs
fractfs.init()
```

plus a `.fractfs.toml` in the repo. No I/O interception, no monkeypatching — it
works at the filesystem layer via symlinks, so it's library-agnostic (duckdb,
polars, pyarrow, raw C all just work).

## Why

Apps moving from Posit Connect to Databricks Apps run on nodes with limited,
ephemeral local disk. Large files don't fit and shouldn't live on the node, and
anything written locally is lost when the node restarts. `fractfs` tags
directories and files into tiers and provisions the filesystem so the right data
lands in the right place — without intercepting I/O calls.

## Install

```bash
pip install fractfs            # core (Databricks Volumes mount / local backend)
pip install 'fractfs[s3]'      # + S3 / fsspec backends
pip install 'fractfs[hashing]' # + xxhash content-based change detection
```

## Configuration

A single `.fractfs.toml` at the app root:

```toml
[dirs]
# Directories whose contents live on the Volume (durable remote store).
# Directory-granular. New files created here later also land remote by default.
paths = ["data/blobs", "exports", "cache/parquet"]

[ignore]
# gitignore-syntax. Matching files are NEVER synced/checkpointed.
patterns = ["*.tmp", "__pycache__/", ".DS_Store"]

[local]
# gitignore-syntax. Matching files are "hot": they live on LOCAL disk
# (fast, atomic rename) but ARE checkpointed for restore.
patterns = ["*.meta.json", "manifest.json", "index.sqlite"]
```

### Environment variables

| Var | Meaning | Example |
|---|---|---|
| `fractfs_BACKEND` | `volumes` \| `s3` \| `local` | `volumes` |
| `fractfs_VOLUME_ROOT` | Mount root (or fsspec URL) for the remote store | `/Volumes/cat/schema/vol` |
| `fractfs_SYNC_INTERVAL` | Checkpoint cadence (seconds) | `300` |
| `fractfs_SCRATCH` | Node-local scratch root for back-symlink targets | `/tmp/fractfs` |
| `fractfs_CHECKPOINT_SUBDIR` | Where checkpoints live under the Volume | `_checkpoint` |
| `fractfs_CONTENT_HASH` | Use content hashing for change detection | `true` |
| `fractfs_ROOT` | App root holding `.fractfs.toml` | `/app` |

Env vars override the TOML scalar fields. (Both `fractfs_` and `FRACTFS_`
prefixes are accepted.)

## The three tiers

| Tier | Source | Lands | Checkpointed? | Mechanism |
|---|---|---|---|---|
| `dirs` | `[dirs].paths` | **Volume** | No (already durable) | directory symlink → `VOL/<dir>` |
| `local` | `[local].patterns` | **Node** | **Yes** | pre-created back-symlink when inside a Volume dir |
| `ignore` | `[ignore].patterns` | **Node** | **No** | back-symlink (kept off Volume) + sync walker skips |
| *(default)* | everything else | **Node** | **Yes** | normal local disk, checkpointed |

### Precedence (the load-bearing rule)

When a path matches more than one tier, highest priority wins:

1. **`ignore`** — never synced, stays local.
2. **`local`** — stays local, synced.
3. **`dirs`** — Volume redirect (the default for everything else under the dir).

Patterns use gitignore syntax (via [`pathspec`](https://pypi.org/project/pathspec/))
matched against the full relative path: `manifest.json` matches at any depth;
`data/blobs/manifest.json` matches only there.

## The `local` tier — what it is and is not

`local` is for small files co-written with large blobs (a `manifest.json` next to
`x.parquet`) that must survive restart but shouldn't take the FUSE cost of going
direct-to-Volume. Local ext4 gives proper atomic `rename`; a Volume FUSE mount
has real per-op overhead and weak atomicity, so for small mutable metadata
local-tier is both faster and safer.

**The limit:** `local` does **not** give blob↔metadata transactional
consistency. On cold restart you restore a checkpoint up to
`fractfs_SYNC_INTERVAL` seconds stale, while the blob on the Volume is current.
Use `local` only for independent / rebuildable small state. If the small file is
a pointer into the blob that must be exactly consistent, either put it in a
`[dirs]` directory too (shared fate, accept the FUSE cost) or make it
reconstructable from the blob on startup.

### Keeping `local` files always-local (lock files, sqlite, manifests)

A `[dirs]` directory is a symlink to the Volume, so files created inside it
follow that link to the Volume *by default*. To keep a `local`/`ignore` file on
fast node-local disk instead, fractfs places a **back-symlink** on the Volume
that points at node-local scratch:

```
VOL/data/blobs/manifest.json  ->  $fractfs_SCRATCH/data/blobs/manifest.json
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
  `[dirs]` dir therefore lands on the Volume. `init()` emits a warning (also in
  `status()["warnings"]`) when a `[local]` glob could be affected.

**The boundary, stated plainly:** anything that spawns sidecar files with names
you don't control *next to* the data — a SQLite db emitting `-wal`/`-shm`, a
library dropping `<name>.lock` beside the file it locks — should **not** live
loose inside a `[dirs]` directory. Put that state in the default local tree (not
under any `[dirs]` path) where it's plain local disk + checkpointed with all
sidecars co-located, or confine it to a pinned `foo/` subdirectory.

## Public API

```python
import fractfs

fractfs.init()        # load config, provision symlinks, restore checkpoint, start sync
# ... app runs unchanged ...
fractfs.sync_now()    # force a checkpoint (e.g. before graceful shutdown)
fractfs.status()      # tier of each tracked path, last sync time, etc.
fractfs.shutdown()    # stop the sync daemon (runs a final checkpoint)
```

`init()` blocks on restore before returning, so the app never reads cold state.
The provisioner refuses to replace a non-empty real local directory with a
symlink unless you pass `fractfs.init(force=True)` (which migrates its contents
to the Volume first).

## Sharp edges

- **Multi-replica.** Back-symlink targets are node-local; the link itself lives on
  the Volume and is visible to other replicas. Fine for single-replica apps —
  document/guard before running multiple replicas against the same Volume.
- **FUSE atomicity.** Checkpoint writes use temp-file-then-`rename`. If your mount
  doesn't honour atomic rename, the backend falls back to a plain copy.
- **Change detection.** Default is size+mtime (cheap, can miss same-size edits).
  Set `fractfs_CONTENT_HASH=true` (and install `fractfs[hashing]`) for content
  hashing on correctness-sensitive trees.

## License

See [LICENSE](LICENSE).
