# Recipe: AWS (S3 remote, EC2/ECS compute)

There are two ways to use S3 with fractfs, and which you pick decides whether the
**`[dirs]` big-file redirect** is available:

| Approach | Backend | `[dirs]` redirect? | Checkpoint/restore? |
|---|---|---|---|
| **FUSE-mount S3** (mountpoint-s3) | `mount` | ✅ yes | ✅ yes |
| **fsspec/S3 direct** (s3fs) | `fsspec` (`s3` alias) | ❌ no | ✅ yes |

Redirect is implemented with directory symlinks, which need a POSIX target. A raw
S3 bucket has no symlinks, so the `fsspec` backend supports **checkpoint/restore of
local state only** — configure `[dirs]` on it and `init()` refuses (those files
would be stranded on ephemeral disk). To redirect big files to S3, give fractfs a
POSIX view of the bucket by FUSE-mounting it.

## Approach A — FUSE-mount S3, then `mount` backend (full feature set)

[mountpoint-s3](https://github.com/awslabs/mountpoint-s3) mounts a bucket as a
POSIX path. Once mounted, S3 *is* a `mount` remote — no fractfs extra needed:

```bash
mount-s3 my-bucket /mnt/s3          # AWS Mountpoint for S3
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/s3/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> S3 via the mount, direct

[local]
patterns = ["manifest.json", "index.sqlite"]   # small hot state, kept local + checkpointed
```

Mind mountpoint-s3's semantics (no random-writes/rename within an object, eventual
listing) — the `[local]` tier is exactly the escape hatch for small mutable files
that don't tolerate that; see [tiers.md](tiers.md).

## Approach B — fsspec/S3 for checkpoint-only (no FUSE)

If you don't want a FUSE mount, use the `s3` backend purely to checkpoint the
local tree to S3 and restore it on cold start. Leave `[dirs]` **out**.

```bash
pip install 'fractfs[s3]'                        # fsspec + s3fs
export FRACTFS_BACKEND=s3                         # alias for the fsspec backend
export FRACTFS_REMOTE_ROOT=s3://my-bucket/my-app
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml  — no [dirs] on the fsspec backend
[local]
patterns = ["manifest.json", "index.sqlite"]
```

Everything in the local tree is checkpointed to
`s3://my-bucket/my-app/_checkpoint/` on the interval and restored on cold start.
Big files that must *not* consume local disk need Approach A.

### Credentials (both approaches)

Don't put credentials in `.fractfs.toml`. `s3fs` (and mountpoint-s3) use the
standard AWS chain, so an **EC2 instance profile** or **ECS task role** with
`s3:GetObject`/`PutObject`/`ListBucket`/`DeleteObject` on the bucket prefix is
picked up automatically. `AWS_*` env vars / `~/.aws` work for local runs.

## EBS handling

An EBS root/data volume survives instance **stop/start**, but is destroyed when an
instance is **terminated and replaced** (autoscaling, spot reclaim, health-check
recycle) unless you go out of your way to snapshot or reattach it. So for any
fleet that scales or uses spot, treat EBS as effectively ephemeral.

Recommended: **don't rely on EBS for durability at all.** Let S3 be the durable
tier via fractfs and size EBS for just the OS + app bundle + the hot working set
that needs to fit locally. Even after a full instance replacement, `init()`
restores runtime state from the S3 checkpoint. With Approach A, keep `[dirs]`
paths off EBS — they redirect straight to the S3 mount and never consume local
space.

Keep a larger/persistent EBS volume only if you specifically want the **bundle**
to persist across stop/start to skip slow re-deploys — see the NVMe + OverlayFS
option below, which pairs that with fast local writes.

## Making use of instance-store NVMe

EC2 instance types with local NVMe (`m5d`, `c6gd`, `i4i`, …) give you the fastest
possible disk, wiped on stop/terminate — exactly the ephemeral-source role
fractfs checkpoints. Two layouts:

### Option 1 — run the whole working dir on NVMe (simplest)

```bash
# NVMe instance store mounted at /mnt/nvme by your launch/user-data.
export FRACTFS_ROOT=/mnt/nvme/app
export FRACTFS_SCRATCH=/mnt/nvme/app/.fractfs-scratch
export FRACTFS_BACKEND=s3                          # checkpoint-only; or 'mount' via mount-s3
export FRACTFS_REMOTE_ROOT=s3://my-bucket/my-app
export FRACTFS_SYNC_INTERVAL=300
```

Deploy the bundle onto `/mnt/nvme/app` and run from there. Runtime state lives on
NVMe and is checkpointed to S3, and the bundle is auto-ignored (re-extracted onto
NVMe each start). See [deployment.md](deployment.md) for the full walkthrough.

### Option 2 — OverlayFS (persist the bundle on EBS, write to NVMe)

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

## Alternative: EFS as the remote (POSIX, no FUSE-mount step)

EFS (or FSx) is already a POSIX filesystem, so it's a `mount` remote out of the
box — full `[dirs]` redirect, no `[s3]` extra, no mountpoint-s3:

```bash
# EFS mounted at /mnt/efs
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/mnt/efs/my-app
```

This trades S3's cost/scale for POSIX semantics and lower per-op latency on small
files. Everything else (tiers, checkpointing, NVMe layout) is identical.
