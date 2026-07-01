# Recipe: Databricks (Volume remote, Databricks Apps)

Databricks compute mounts Unity Catalog **Volumes** as a POSIX path under
`/Volumes/...`, so fractfs uses the **`mount`** backend — no extra dependency, no
credentials to configure (the workspace identity governs Volume access).

## Install & config

```bash
pip install fractfs          # core only; the Volume is a plain POSIX mount
```

```bash
export FRACTFS_BACKEND=mount
export FRACTFS_REMOTE_ROOT=/Volumes/<catalog>/<schema>/<volume>/<app>
export FRACTFS_SYNC_INTERVAL=300
```

```toml
# .fractfs.toml
[dirs]
paths = ["data/blobs", "exports"]   # big files -> Volume, direct, never checkpointed

[local]
patterns = ["manifest.json", "index.sqlite"]   # small hot state, kept local + checkpointed
```

Create the Volume once (SQL or the Catalog UI) and give it an app-specific
subdirectory:

```sql
CREATE VOLUME IF NOT EXISTS <catalog>.<schema>.<volume>;
```

## Deploying on Databricks Apps

Databricks Apps run on **ephemeral compute**: only your deployed source bundle is
restored on restart — anything written to local disk at runtime is lost. That is
exactly the gap fractfs closes, and `auto_ignore_bundle` (on by default) is a
perfect fit, since the bundle is re-supplied from the image every cold start.

Wire the env vars through `app.yaml`:

```yaml
# app.yaml
command: ["python", "app.py"]     # or ["streamlit", "run", "app.py"], etc.
env:
  - name: FRACTFS_BACKEND
    value: mount
  - name: FRACTFS_REMOTE_ROOT
    value: /Volumes/main/default/app_state/myapp
  - name: FRACTFS_SYNC_INTERVAL
    value: "300"
```

The app runs from its deployed source directory, so leave `FRACTFS_ROOT` unset
(it defaults to the working directory holding `.fractfs.toml`). On start,
`init()` provisions the Volume symlinks and restores the checkpoint before your
app reads anything; the background daemon then checkpoints runtime state to the
Volume every interval.

Grant the app's service principal `READ VOLUME` / `WRITE VOLUME` on the Volume:

```sql
GRANT READ VOLUME, WRITE VOLUME ON VOLUME <catalog>.<schema>.<volume>
  TO `<app-service-principal>`;
```

## Databricks-specific notes

- **FUSE atomicity.** Volume mounts are FUSE-backed and don't always honour atomic
  `rename`. fractfs checkpoints via temp-file-then-`rename` within the Volume; see
  the change-detection / FUSE notes in [deployment.md](deployment.md#sharp-edges).
  Prefer the `[local]` tier over direct-to-Volume for small mutable metadata —
  [tiers.md](tiers.md) explains why local is both faster and safer there.
- **Multi-replica.** If you scale an app past one instance, note the back-symlink
  sharp edge (targets are node-local) in [deployment.md](deployment.md#sharp-edges)
  before pointing multiple replicas at the same Volume.
- **Off-mount access.** If you ever run somewhere a Volume is *not* FUSE-mounted,
  the Databricks Files API (`WorkspaceClient.files`) is the fallback path — not
  yet shipped as a backend, but it fits the `mount` family.
