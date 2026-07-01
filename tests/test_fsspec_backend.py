"""Regression tests for the fsspec backend.

The bug: ``Config`` used to coerce ``remote_root`` through ``pathlib.Path``, which
collapses ``s3://bucket/x`` to ``s3:/bucket/x`` — a string fsspec then resolves as
a *local* file path, silently writing checkpoints to disk instead of the store.
And ``[dirs]`` redirect (symlink-based) was run regardless of backend, stranding
big files on ephemeral disk for object-store backends.
"""

import importlib

import pytest

from fractfs.config import Config, load_config


def test_fsspec_url_not_mangled_by_pathlib(tmp_path):
    (tmp_path / ".fractfs.toml").write_text(
        'backend = "s3"\nremote_root = "s3://my-bucket/my-app"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.backend == "fsspec"          # alias resolved
    assert cfg.remote_root == "s3://my-bucket/my-app"  # kept verbatim, still a str
    assert isinstance(cfg.remote_root, str)


def test_mount_root_is_a_path(tmp_path):
    cfg = Config(root=tmp_path, backend="mount", remote_root="/mnt/store")
    from pathlib import Path

    assert isinstance(cfg.remote_root, Path)


def test_fsspec_supports_checkpoint_not_redirect(tmp_path):
    cfg = Config(root=tmp_path, backend="fsspec", remote_root="s3://b/x")
    assert cfg.has_remote_store() is True
    assert cfg.supports_redirect() is False


def test_dirs_with_fsspec_backend_raises(tmp_path, monkeypatch):
    import fractfs

    (tmp_path / ".fractfs.toml").write_text(
        'backend = "s3"\n'
        'remote_root = "s3://b/x"\n'
        "[dirs]\n"
        'paths = ["data/blobs"]\n'
    )
    monkeypatch.setenv("fractfs_ROOT", str(tmp_path))
    importlib.reload(fractfs)
    with pytest.raises(ValueError, match="not supported by the 'fsspec' backend"):
        fractfs.init(start_daemon=False)


def test_fsspec_checkpoint_restore_roundtrip(tmp_path, monkeypatch):
    """Drive the real FsspecBackend via a file:// URL (no S3 needed).

    Exercises the code path that the Path-mangling bug silently broke: writing the
    checkpoint through fsspec and restoring from it.
    """
    pytest.importorskip("fsspec")
    import fractfs

    app = tmp_path / "app"
    app.mkdir()
    store = tmp_path / "store"
    # No [dirs]: fsspec supports checkpoint/restore of the default local tier only.
    (app / ".fractfs.toml").write_text(
        f'backend = "fsspec"\nremote_root = "file://{store}"\nsync_interval = 0\n'
    )
    monkeypatch.setenv("fractfs_ROOT", str(app))

    importlib.reload(fractfs)
    fractfs.init(start_daemon=False)
    (app / "state.txt").write_text("durable")
    fractfs.sync_now()

    # The checkpoint physically landed under the file:// store, NOT in a bogus
    # local "file:" directory next to the app.
    assert (store / "_checkpoint" / "state.txt").read_text() == "durable"

    # cold restart: drop the local file, re-init -> restored from the fsspec store
    (app / "state.txt").unlink()
    importlib.reload(fractfs)
    fractfs.init(start_daemon=False)
    assert (app / "state.txt").read_text() == "durable"
