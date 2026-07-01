# Recipe: Google Cloud (GCS remote, GKE/Cloud Run/GCE)

As on AWS, which approach you pick decides whether the **`[dirs]` big-file
redirect** is available. Redirect uses directory symlinks (POSIX-only), so a raw
GCS bucket via `gcsfs` supports **checkpoint/restore of local state only**; to
redirect big files you give fractfs a POSIX view of the bucket with
[gcsfuse](https://cloud.google.com/storage/docs/gcsfuse).

| Approach | Backend | `[dirs]` redirect? | Checkpoint/restore? |
|---|---|---|---|
| **FUSE-mount GCS** (gcsfuse) | `mount` | ✅ yes | ✅ yes |
| **fsspec/GCS direct** (gcsfs) | `fsspec` | ❌ no | ✅ yes |

## Approach A — FUSE-mount GCS, then `mount` backend

```bash
gcsfuse my-bucket /mnt/gcs
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/gcs/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> GCS via the mount, direct

[local]
patterns = ["manifest.json", "index.sqlite"]
```

On GKE, gcsfuse is available as a [CSI driver](https://cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/cloud-storage-fuse-csi-driver)
you attach as a volume, so the pod sees `/mnt/gcs` with no sidecar of your own.

## Approach B — fsspec/GCS for checkpoint-only (no FUSE)

```bash
pip install fractfs gcsfs    # gcsfs pulls fsspec transitively (no need for [s3])
export FRACTFS_BACKEND=fsspec
export FRACTFS_REMOTE_ROOT=gs://my-bucket/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml  — no [dirs] on the fsspec backend
[local]
patterns = ["manifest.json", "index.sqlite"]
```

The `[s3]`/`[fsspec]` extra ships `s3fs`; for GCS install `gcsfs` directly (it
brings `fsspec` with it).

### Credentials (both approaches)

`gcsfs` and gcsfuse both use **Application Default Credentials**, so on GKE give
the pod a **Workload Identity** service account (or run on a GCE VM with an
attached service account) that has `roles/storage.objectAdmin` on the bucket. No
keys in `.fractfs.toml`.

## Compute notes

- **Cloud Run / GKE** containers have ephemeral local disk (often tmpfs-backed on
  Cloud Run) — the default local tier is checkpointed to GCS and restored on cold
  start. On Cloud Run, size the in-memory `/tmp` for your hot working set, or
  attach a writable volume.
- **Local SSD (NVMe)** on GKE node pools or GCE instances is fast and ephemeral
  (wiped on stop/replace) — the same ephemeral-source role as elsewhere. Use the
  "whole working dir on NVMe" layout from [deployment.md](deployment.md), pointing
  `FRACTFS_ROOT`/`FRACTFS_SCRATCH` at the Local SSD mount.

## Alternative: Filestore as the remote (POSIX, no FUSE-mount step)

Filestore (managed NFS) is already a POSIX filesystem, so it's a `mount` remote
out of the box — full `[dirs]` redirect, no `gcsfs`, no gcsfuse:

```bash
# Filestore mounted at /mnt/filestore
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/filestore/my-app
```
