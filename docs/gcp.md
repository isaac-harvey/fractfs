# Recipe: Google Cloud (GCS remote, GKE/Cloud Run/GCE)

Big/durable files go to **Google Cloud Storage** via the `fsspec` backend, backed
by [`gcsfs`](https://gcsfs.readthedocs.io/). Everything else mirrors the AWS
recipe — only the URL scheme and the protocol package change.

## Install & credentials

```bash
pip install fractfs gcsfs    # gcsfs pulls fsspec transitively (no need for [s3])
```

The `[s3]`/`[fsspec]` extra ships `s3fs`; for GCS you want `gcsfs` instead, so
install it directly. `gcsfs` uses **Application Default Credentials**, so on GKE
give the pod a **Workload Identity** service account (or run on a GCE VM with an
attached service account) that has `roles/storage.objectAdmin` on the bucket. No
keys in `.fractfs.toml`.

## Baseline config

```bash
export FRACTFS_BACKEND=fsspec
export FRACTFS_REMOTE_ROOT=gs://my-bucket/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> GCS, direct, never checkpointed

[local]
patterns = ["manifest.json", "index.sqlite"]   # small hot state, kept local + checkpointed
```

## Compute notes

- **Cloud Run / GKE** containers have ephemeral local disk (often tmpfs-backed on
  Cloud Run) — the default local tier is checkpointed to GCS and restored on cold
  start, exactly as intended. On Cloud Run, size the in-memory `/tmp` for your hot
  working set, or attach a writable volume.
- **Local SSD (NVMe)** on GKE node pools or GCE instances is fast and ephemeral
  (wiped on stop/replace) — the same ephemeral-source role as elsewhere. Use the
  "whole working dir on NVMe" layout from [deployment.md](deployment.md), pointing
  `FRACTFS_ROOT`/`FRACTFS_SCRATCH` at the Local SSD mount and `FRACTFS_REMOTE_ROOT`
  at `gs://…`.

## Alternative: Filestore as the remote (no GCS)

To keep POSIX semantics, mount **Filestore** (managed NFS) and use the `mount`
backend instead — no `gcsfs` needed:

```bash
# Filestore mounted at /mnt/filestore
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/filestore/my-app
```
