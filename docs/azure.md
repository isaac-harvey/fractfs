# Recipe: Azure (ADLS/Blob remote, AKS/Container Apps)

Big/durable files go to **Azure Data Lake Storage Gen2 / Blob Storage** via the
`fsspec` backend, backed by [`adlfs`](https://github.com/fsspec/adlfs).

## Install & credentials

```bash
pip install fractfs adlfs    # adlfs pulls fsspec transitively (no need for [s3])
```

`adlfs` authenticates with `azure-identity`'s `DefaultAzureCredential`, so a
**managed identity** (AKS Workload Identity, or a VM/Container Apps system-assigned
identity) with the **Storage Blob Data Contributor** role on the account/container
is picked up automatically. For local runs it also honours `AZURE_STORAGE_*` env
vars / a connection string.

## Baseline config

```bash
export FRACTFS_BACKEND=fsspec
export FRACTFS_REMOTE_ROOT=abfs://my-container/my-app   # or az://my-container/my-app
export FRACTFS_SYNC_INTERVAL=300
# adlfs needs to know the account (unless encoded in a connection string):
export AZURE_STORAGE_ACCOUNT_NAME=mystorageacct
```

```toml
# .fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> ADLS, direct, never checkpointed

[local]
patterns = ["manifest.json", "index.sqlite"]   # small hot state, kept local + checkpointed
```

## Compute notes

- **AKS / Container Apps** give containers ephemeral local disk; the default local
  tier is checkpointed to ADLS and restored on cold start.
- **Local NVMe** on `Lsv3`/`Ddsv5`-class VMs or AKS ephemeral-OS-disk node pools is
  fast and ephemeral — use the "whole working dir on NVMe" layout from
  [deployment.md](deployment.md) with `FRACTFS_REMOTE_ROOT` set to the `abfs://`
  URL.

## Alternative: Azure Files as the remote (no ADLS)

To keep POSIX semantics, mount **Azure Files** (SMB/NFS) and use the `mount`
backend instead — no `adlfs` needed:

```bash
# Azure Files mounted at /mnt/azfiles
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/azfiles/my-app
```
