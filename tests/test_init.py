import importlib

import fractfs


CONFIG = """
backend = "volumes"
volume_root = "{vol}"
scratch = "{scratch}"
sync_interval = 0

[dirs]
paths = ["data/blobs"]

[local]
patterns = ["manifest.json"]

[ignore]
patterns = ["*.tmp"]
"""


def _write_cfg(root, vol, scratch):
    (root / ".fractfs.toml").write_text(CONFIG.format(vol=vol, scratch=scratch))


def test_init_provisions_and_status(layout, monkeypatch):
    root, vol, scratch = layout
    _write_cfg(root, vol, scratch)
    monkeypatch.setenv("fractfs_ROOT", str(root))

    importlib.reload(fractfs)
    fractfs.init(start_daemon=False)
    assert (root / "data" / "blobs").is_symlink()

    st = fractfs.status()
    assert st["backend"] == "volumes"
    assert st["dirs"]["data/blobs"] == "volume"
    assert st["provisionable"] is True
    assert st["daemon_running"] is False


def test_init_restore_roundtrip(layout, monkeypatch):
    root, vol, scratch = layout
    _write_cfg(root, vol, scratch)
    monkeypatch.setenv("fractfs_ROOT", str(root))

    importlib.reload(fractfs)
    fractfs.init(start_daemon=False)
    (root / "notes.txt").write_text("remember me")
    fractfs.sync_now()

    # cold restart: nuke local file, re-init -> restored
    (root / "notes.txt").unlink()
    importlib.reload(fractfs)
    fractfs.init(start_daemon=False)
    assert (root / "notes.txt").read_text() == "remember me"


def test_passthrough_without_volume(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    (root / ".fractfs.toml").write_text('backend = "local"\n')
    monkeypatch.setenv("fractfs_ROOT", str(root))
    monkeypatch.delenv("fractfs_VOLUME_ROOT", raising=False)

    importlib.reload(fractfs)
    fractfs.init(start_daemon=False)
    st = fractfs.status()
    assert st["provisionable"] is False
    assert fractfs.sync_now() == []
