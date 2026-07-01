# Deployment

See the [README](../README.md) for install, configuration, and the tier model.
This document covers deploy-bundle handling, the fast-ephemeral-disk layout, and
the known sharp edges.

## The deploy bundle is auto-ignored

The platform re-supplies your deployed app bundle (code, assets, `.fractfs.toml`)
from the image on every cold start, so checkpointing it would copy your whole app
to durable storage every interval for nothing. fractfs detects the bundle and
excludes it automatically.

**How:** at each `init()` (after provisioning, before restore) fractfs takes the
set of files already present in the local tree and subtracts anything it already
knows is runtime state (everything in the checkpoint manifest). On a cold
ephemeral node the remainder is exactly the freshly-deployed bundle; on a warm /
persistent disk the subtraction keeps real runtime files *out* of the bundle so
they keep being checkpointed. It's recomputed every start, so it tracks redeploys
that add or remove files with no config changes. `status()` reports
`bundle_file_count`.

**Turn it off** with `FRACTFS_AUTO_IGNORE_BUNDLE=false` (or `auto_ignore_bundle =
false` in the TOML) if you want every local file checkpointed regardless.

**Caveat (persistent disks only):** a runtime file created but never checkpointed
before a restart that *survives* on a persistent local disk could be
misclassified as bundle and then skipped. This can't happen on an ephemeral disk
(uncheckpointed files are already gone), and a persistent local disk usually
doesn't need the checkpoint anyway — but disable the feature if you rely on one.

## Deploying on fast ephemeral disk (NVMe instance store)

When a node has local NVMe, the cleanest layout is to put the **entire working
directory on NVMe** — fastest possible disk for all hot state — and let fractfs
divert big files to durable storage and checkpoint the rest. NVMe being wiped on
stop/replace is exactly what the checkpoint covers.

No new fractfs concept is needed: NVMe simply *becomes* the local disk. (NVMe is
never a durable *target* — you never checkpoint *to* it; it's a fast, ephemeral
*source* that gets checkpointed, the same role as the default local tree.)

```bash
# 1. NVMe instance store mounted at /mnt/nvme (instance/launch config).
# 2. Run the app from there so the bundle and all writes live on NVMe.
export FRACTFS_ROOT=/mnt/nvme/app
export FRACTFS_SCRATCH=/mnt/nvme/app/.fractfs-scratch   # back-symlink targets on NVMe too
# 3. Durable store for big files + checkpoints (NOT on NVMe):
export FRACTFS_BACKEND=s3
export FRACTFS_REMOTE_ROOT=s3://my-bucket/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# /mnt/nvme/app/.fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> S3, direct
```

Then:

- **Big files** (`[dirs]`) go straight to S3 — never on NVMe, never checkpointed.
- **Runtime state** (default tier) lives on fast NVMe and is checkpointed to S3.
- **The bundle** is auto-ignored (re-supplied by the image each start).
- **On cold start**, the platform re-extracts the bundle onto NVMe and fractfs
  restores runtime state from the S3 checkpoint before your app reads anything.

Two notes:

- **Reads of the bundle stay local on NVMe; you don't need EBS for it.** Keep EBS
  only if you genuinely want the bundle to *persist* (e.g. slow re-deploys) — and
  if so, an OverlayFS mount (EBS lower, NVMe upper) gives "reads fall through to
  EBS, all writes land on NVMe" transparently. That's an infra-level mount set up
  before the app starts; fractfs composes on top of it unchanged.
- **Splitting hot dirs across two local disks** (some on NVMe, some on EBS, at the
  same time) is the one case that would need a future "fast-local redirect target"
  tier. The single-disk layout above needs none of it.

## Sharp edges

- **Multi-replica.** Back-symlink targets are node-local; the link itself lives on
  the remote store and is visible to other replicas. Fine for single-replica apps —
  document/guard before running multiple replicas against the same remote store.
- **FUSE atomicity.** Checkpoint writes use temp-file-then-`rename`. If your mount
  doesn't honour atomic rename, the backend falls back to a plain copy.
- **Change detection.** Default is size+mtime (cheap, can miss same-size edits).
  Set `FRACTFS_CONTENT_HASH=true` (and install `fractfs[hashing]`) for content
  hashing on correctness-sensitive trees.
