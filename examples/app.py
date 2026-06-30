"""Minimal drop-in example.

The only fractfs-specific lines are the import and init() at the top; everything
below is ordinary file I/O that lands in the right tier automatically.

Run with a local backend to try it without a real Volume::

    export fractfs_BACKEND=volumes
    export fractfs_VOLUME_ROOT=/tmp/fractfs-vol
    export fractfs_ROOT="$(dirname "$0")"
    python app.py
"""

import json

import fractfs

fractfs.init()  # <-- the whole integration

# ... the rest is your app, unchanged ...


def main():
    # A large blob: lands on the Volume (directory-redirected).
    with open("data/blobs/dataset.bin", "wb") as fh:
        fh.write(b"\x00" * (1 << 20))

    # A small manifest next to it: kept local, checkpointed for restart safety.
    with open("data/blobs/manifest.json", "w") as fh:
        json.dump({"rows": 1000, "version": 2}, fh)

    # Ordinary app state: local + checkpointed by default.
    with open("last_run.txt", "w") as fh:
        fh.write("ok")

    print(json.dumps(fractfs.status(), indent=2, default=str))
    fractfs.sync_now()  # force a checkpoint before exit


if __name__ == "__main__":
    main()
