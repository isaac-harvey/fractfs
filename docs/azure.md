# Recipe: Azure (ADLS/Blob remote, AKS/Container Apps)

As on AWS/GCP, which approach you pick decides whether the **`[dirs]` big-file
redirect** is available. Redirect uses directory symlinks (POSIX-only), so ADLS/
Blob via `adlfs` supports **checkpoint/restore of local state only**; to redirect
big files you give fractfs a POSIX view of the container with
[blobfuse2](https://github.com/Azure/azure-storage-fuse).

| Approach | Backend | `[dirs]` redirect? | Checkpoint/restore? |
|---|---|---|---|
| **FUSE-mount Blob** (blobfuse2) | `mount` | ✅ yes | ✅ yes |
| **fsspec/ADLS direct** (adlfs) | `fsspec` | ❌ no | ✅ yes |

## Approach A — FUSE-mount Blob, then `mount` backend

```bash
blobfuse2 mount /mnt/blob --config-file=./blobfuse.yaml
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/blob/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> Blob via the mount, direct

[local]
patterns = ["manifest.json", "index.sqlite"]
```

On AKS, the [Blob CSI driver](https://learn.microsoft.com/azure/aks/azure-blob-csi)
can mount the container as a volume (blobfuse2 under the hood), so the pod sees
`/mnt/blob` directly.

## Approach B — fsspec/ADLS for checkpoint-only (no FUSE)

```bash
pip install fractfs adlfs    # adlfs pulls fsspec transitively (no need for [s3])
export FRACTFS_BACKEND=fsspec
export FRACTFS_REMOTE_ROOT=abfs://my-container/my-app   # or az://my-container/my-app
export FRACTFS_SYNC_INTERVAL=300
# adlfs needs to know the account (unless encoded in a connection string):
export AZURE_STORAGE_ACCOUNT_NAME=mystorageacct
```

```toml
# .fractfs.toml  — no [dirs] on the fsspec backend
[local]
patterns = ["manifest.json", "index.sqlite"]
```

### Credentials (both approaches)

`adlfs` and blobfuse2 authenticate with `azure-identity`'s
`DefaultAzureCredential`, so a **managed identity** (AKS Workload Identity, or a
VM/Container Apps system-assigned identity) with the **Storage Blob Data
Contributor** role on the account/container is picked up automatically. For local
runs `adlfs` also honours `AZURE_STORAGE_*` env vars / a connection string.

## Compute notes

- **AKS / Container Apps** give containers ephemeral local disk; the default local
  tier is checkpointed to the remote and restored on cold start.
- **Local NVMe** on `Lsv3`/`Ddsv5`-class VMs or AKS ephemeral-OS-disk node pools is
  fast and ephemeral — use the "whole working dir on NVMe" layout from
  [deployment.md](deployment.md).

## Alternative: Azure Files as the remote (POSIX, no FUSE-mount step)

Azure Files (SMB/NFS) is already a POSIX filesystem, so it's a `mount` remote out
of the box — full `[dirs]` redirect, no `adlfs`, no blobfuse2:

```bash
# Azure Files mounted at /mnt/azfiles
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/azfiles/my-app
```
