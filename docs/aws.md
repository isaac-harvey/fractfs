# Recipe: AWS (S3 remote, EC2/ECS compute)

The standard AWS setup: big/durable files go to **S3** via the `fsspec` backend
(`s3` alias), hot local state is checkpointed to S3, and the node's disk — EBS or
instance-store NVMe — is treated as ephemeral.

## Install & credentials

```bash
pip install 'fractfs[s3]'    # fsspec + s3fs
```

Don't put credentials in `.fractfs.toml`. `s3fs` uses the standard boto3
credential chain, so an **EC2 instance profile** or **ECS task role** with
`s3:GetObject`/`PutObject`/`ListBucket`/`DeleteObject` on the bucket prefix is
picked up automatically. Falls back to `AWS_*` env vars / `~/.aws` for local runs.

## Baseline config

```bash
export FRACTFS_BACKEND=s3                        # alias for the fsspec backend
export FRACTFS_REMOTE_ROOT=s3://my-bucket/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> S3, direct, never checkpointed

[local]
patterns = ["manifest.json", "index.sqlite"]   # small hot state, kept local + checkpointed
```

Everything not under `[dirs]` stays on the node's disk and is checkpointed to
`s3://my-bucket/my-app/_checkpoint/` on the sync interval, then restored on cold
start.

## EBS handling

An EBS root/data volume survives instance **stop/start**, but is destroyed when an
instance is **terminated and replaced** (autoscaling, spot reclaim, health-check
recycle) unless you go out of your way to snapshot or reattach it. So for any
fleet that scales or uses spot, treat EBS as effectively ephemeral.

Recommended: **don't rely on EBS for durability at all.** Let S3 be the durable
tier via fractfs and size EBS for just the OS + app bundle + the hot working set
that needs to fit locally. Even after a full instance replacement, `init()`
restores runtime state from the S3 checkpoint. Keep `[dirs]` paths off EBS — they
redirect straight to S3 and never consume local space.

Keep a larger/persistent EBS volume only if you specifically want the **bundle**
to persist across stop/start to skip slow re-deploys — see the NVMe + OverlayFS
option below, which pairs that with fast local writes.

## Making use of instance-store NVMe

EC2 instance types with local NVMe (`m5d`, `c6gd`, `i4i`, …) give you the fastest
possible disk, wiped on stop/terminate — exactly the ephemeral-source role
fractfs checkpoints. Two layouts:

### Option A — run the whole working dir on NVMe (simplest)

```bash
# NVMe instance store mounted at /mnt/nvme by your launch/user-data.
export FRACTFS_ROOT=/mnt/nvme/app
export FRACTFS_SCRATCH=/mnt/nvme/app/.fractfs-scratch
export FRACTFS_BACKEND=s3
export FRACTFS_REMOTE_ROOT=s3://my-bucket/my-app
export FRACTFS_SYNC_INTERVAL=300
```

Deploy the bundle onto `/mnt/nvme/app` and run from there. Big files go straight
to S3, runtime state lives on NVMe and is checkpointed to S3, and the bundle is
auto-ignored (re-extracted onto NVMe each start). See
[deployment.md](deployment.md) for the full walkthrough.

### Option B — OverlayFS (persist the bundle on EBS, write to NVMe)

If you want the bundle to persist on EBS across stop/start *and* all writes to
land on fast NVMe, stack them with OverlayFS before the app starts:

```bash
# EBS holds the bundle (lower, read-only); NVMe takes all writes (upper).
mount -t overlay overlay \
  -o lowerdir=/mnt/ebs/app,upperdir=/mnt/nvme/upper,workdir=/mnt/nvme/work \
  /mnt/nvme/app
export FRACTFS_ROOT=/mnt/nvme/app
export FRACTFS_SCRATCH=/mnt/nvme/app/.fractfs-scratch
export FRACTFS_BACKEND=s3
export FRACTFS_REMOTE_ROOT=s3://my-bucket/my-app
```

fractfs composes on top of the overlay unchanged: reads fall through to EBS,
writes land on NVMe, and the checkpoint still covers everything on the upper
layer.

## Alternative: EFS as the remote (no S3)

If you'd rather keep a POSIX filesystem than an object store, mount **EFS** (or
FSx) and use the `mount` backend instead — no `[s3]` extra needed:

```bash
# EFS mounted at /mnt/efs
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/efs/my-app
```

This trades S3's cost/scale for POSIX semantics and lower per-op latency on small
files. Everything else (tiers, checkpointing, NVMe layout) is identical.
