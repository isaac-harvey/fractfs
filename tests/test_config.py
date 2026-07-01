import pytest

from fractfs.config import load_config


TOML = """
backend = "mount"
remote_root = "{vol}"
scratch = "{scratch}"
sync_interval = 42

[dirs]
paths = ["data/blobs", "exports/"]

[ignore]
patterns = ["*.tmp"]

[local]
patterns = ["manifest.json"]
"""


def write_toml(root, vol, scratch):
    (root / ".fractfs.toml").write_text(
        TOML.format(vol=vol, scratch=scratch)
    )


def test_load_from_toml(layout):
    root, vol, scratch = layout
    write_toml(root, vol, scratch)
    cfg = load_config(root)
    assert cfg.backend == "mount"
    assert cfg.remote_root == vol
    assert cfg.sync_interval == 42
    # trailing slash stripped, normalized
    assert cfg.dir_paths == ["data/blobs", "exports"]
    assert cfg.ignore_spec.match_file("a/b.tmp")
    assert cfg.local_spec.match_file("data/blobs/manifest.json")


def test_env_overrides_toml(layout, monkeypatch):
    root, vol, scratch = layout
    write_toml(root, vol, scratch)
    monkeypatch.setenv("fractfs_SYNC_INTERVAL", "7")
    monkeypatch.setenv("fractfs_BACKEND", "s3")  # alias -> fsspec
    cfg = load_config(root)
    assert cfg.sync_interval == 7
    assert cfg.backend == "fsspec"


def test_missing_toml_is_empty_config(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.dir_paths == []
    assert cfg.backend == "mount"
    assert not cfg.is_provisionable()


def test_unknown_backend_raises(layout):
    root, vol, scratch = layout
    (root / ".fractfs.toml").write_text('backend = "frobnicate"\n')
    with pytest.raises(ValueError):
        load_config(root)
