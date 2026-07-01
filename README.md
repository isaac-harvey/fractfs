# fractfs

**Drop-in tiered file storage for apps on ephemeral nodes.**

`fractfs` lets an app with limited, ephemeral local disk transparently push large
files to durable remote storage while keeping small hot state local, with periodic
checkpoint/restore so a node stop/start doesn't lose data.

In essence, the file system is *fractured* into two layers, handled so that you
don't have to think about it.

The only change to your application is one line at startup:

```python
import fractfs
fractfs.init()
```

Selection of what goes where is handled by a `.fractfs.toml` file in the repo.
No I/O interception, no monkeypatching — it works at the filesystem layer via
symlinks, so it's library-agnostic (duckdb, polars, raw C all just work).

## Any Remote Storage

`fractfs` doesn't care *what* the durable store is, only *how* it's reached:

| Backend | Reached as | `[dirs]` redirect | Checkpoint/restore | Install |
|---|---|---|---|---|
| `mount` | a POSIX filesystem path | ✅ yes | ✅ yes | core (no deps) |
| `fsspec` | an fsspec URL | ❌ no | ✅ yes | `fractfs[fsspec]` |

`mount` examples: a Databricks Volume mount, NFS/EFS, SMB, a plain local dir.
`fsspec` examples: S3, GCS, ADLS. `s3` is a kept alias for `fsspec`.

The **`[dirs]` big-file redirect** is implemented with directory symlinks, which
need a POSIX target — so it requires the `mount` backend. To redirect big files to
an object store, FUSE-mount it (mountpoint-s3 / gcsfuse / blobfuse2) and use the
`mount` backend; the `fsspec` backend on its own gives you checkpoint/restore of
local state, not redirect. See the [platform recipes](#platform-recipes).

## Install

```bash
pip install fractfs             # core: the mount backend (any POSIX path)
pip install 'fractfs[s3]'       # + fsspec/s3fs for S3/GCS/ADLS object stores
pip install 'fractfs[hashing]'  # + xxhash content-based change detection
```

## Configuration

A single `.fractfs.toml` at the app root:

```toml
[dirs]
# Directories whose contents live on the durable remote store.
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

### Environment Variables

| Var | Meaning | Example |
|---|---|---|
| `FRACTFS_BACKEND` | `mount` \| `fsspec` (alias `s3`) | `mount` |
| `FRACTFS_REMOTE_ROOT` | Mount path or fsspec URL for the durable store | `/Volumes/cat/schema/vol`, `s3://bucket/app` |
| `FRACTFS_SYNC_INTERVAL` | Checkpoint cadence (seconds) | `300` |
| `FRACTFS_SCRATCH` | Node-local scratch root for back-symlink targets | `/tmp/fractfs` |
| `FRACTFS_CHECKPOINT_SUBDIR` | Where checkpoints live under the remote store | `_checkpoint` |
| `FRACTFS_CONTENT_HASH` | Use content hashing for change detection | `true` |
| `FRACTFS_AUTO_IGNORE_BUNDLE` | Exclude the deploy bundle from the checkpoint | `true` |
| `FRACTFS_ROOT` | App root holding `.fractfs.toml` | `/app` |

Env vars override the TOML scalar fields. (Both `FRACTFS_` and `fractfs_`
prefixes are accepted.) With no `FRACTFS_REMOTE_ROOT` set, fractfs runs in
passthrough mode — no redirect, no checkpoint.

## Tiers

| Tier | Source | Lands | Checkpointed? | Mechanism |
|---|---|---|---|---|
| `dirs` | `[dirs].paths` | **Remote** | No (already durable) | directory symlink → `REMOTE/<dir>` |
| `local` | `[local].patterns` | **Node** | **Yes** | pre-created back-symlink when inside a remote dir |
| `ignore` | `[ignore].patterns` | **Node** | **No** | back-symlink (kept off remote) + sync walker skips |
| *(default)* | everything else | **Node** | **Yes** | normal local disk, checkpointed |

### Precedence (the load-bearing rule)

When a path matches more than one tier, highest priority wins:

1. **`ignore`** — never synced, stays local.
2. **`local`** — stays local, synced.
3. **`dirs`** — remote redirect (the default for everything else under the dir).

Patterns use gitignore syntax (via [`pathspec`](https://pypi.org/project/pathspec/))
matched against the full relative path: `manifest.json` matches at any depth;
`data/blobs/manifest.json` matches only there.

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
to the remote store first).

## Additional Docs

- **[docs/tiers.md](docs/tiers.md)** — what the `local` tier does and doesn't
  guarantee, and how to keep specific files (lock files, sqlite, manifests)
  always-local inside a remote-redirected directory.
- **[docs/deployment.md](docs/deployment.md)** — deploy-bundle auto-ignore, the
  fast-ephemeral-disk (NVMe) layout, and the known sharp edges (multi-replica,
  FUSE atomicity, change detection).

### Platform-Specific Guides

Copy-paste config for common setups:

- **[docs/aws.md](docs/aws.md)** — S3 remote, EBS vs instance-store NVMe, OverlayFS, EFS alternative.
- **[docs/databricks.md](docs/databricks.md)** — Volume remote, deploying on Databricks Apps.
- **[docs/gcp.md](docs/gcp.md)** — GCS remote (gcsfs), GKE/Cloud Run/Local SSD, Filestore alternative.
- **[docs/azure.md](docs/azure.md)** — ADLS/Blob remote (adlfs), AKS/Container Apps, Azure Files alternative.

## License

See [LICENSE](LICENSE).
