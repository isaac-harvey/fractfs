import os

import pytest

from fractfs.provisioner import ClobberError, provision
from conftest import make_config


def test_dir_becomes_symlink_to_remote(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"])
    provision(cfg)
    link = root / "data" / "blobs"
    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(vol / "data" / "blobs")


def test_idempotent(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"])
    provision(cfg)
    actions = provision(cfg)
    assert any("already linked" in a for a in actions)
    assert (root / "data" / "blobs").is_symlink()


def test_writes_through_symlink_land_on_remote(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"])
    provision(cfg)
    (root / "data" / "blobs" / "x.parquet").write_text("payload")
    assert (vol / "data" / "blobs" / "x.parquet").read_text() == "payload"


def test_clobber_guard_on_nonempty_real_dir(layout):
    root, vol, scratch = layout
    (root / "data" / "blobs").mkdir(parents=True)
    (root / "data" / "blobs" / "real.parquet").write_text("important")
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"])
    with pytest.raises(ClobberError):
        provision(cfg)
    # data untouched
    assert (root / "data" / "blobs" / "real.parquet").read_text() == "important"


def test_force_migrates_existing_data(layout):
    root, vol, scratch = layout
    (root / "data" / "blobs").mkdir(parents=True)
    (root / "data" / "blobs" / "real.parquet").write_text("important")
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"])
    provision(cfg, force=True)
    link = root / "data" / "blobs"
    assert link.is_symlink()
    assert (vol / "data" / "blobs" / "real.parquet").read_text() == "important"
    # and it is readable through the link
    assert (link / "real.parquet").read_text() == "important"


def test_back_symlink_pins_local_file_inside_dir(layout):
    root, vol, scratch = layout
    cfg = make_config(
        root, vol, scratch, dirs=["data/blobs"], local=["manifest.json"]
    )
    provision(cfg)
    # simulate a pre-existing manifest already on the remote store
    manifest = vol / "data" / "blobs" / "manifest.json"
    manifest.write_text("v1")
    # re-provision: it should get pinned back to scratch
    provision(cfg)
    link = vol / "data" / "blobs" / "manifest.json"
    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(
        scratch / "data" / "blobs" / "manifest.json"
    )
    # data preserved through the move
    assert (scratch / "data" / "blobs" / "manifest.json").read_text() == "v1"
    # readable through the app path
    assert (root / "data" / "blobs" / "manifest.json").read_text() == "v1"


def test_relink_when_pointing_elsewhere(layout):
    root, vol, scratch = layout
    stale = scratch / "stale_target"
    stale.mkdir()
    (root / "data").mkdir(parents=True)
    (root / "data" / "blobs").symlink_to(stale)
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"])
    provision(cfg)
    assert os.path.realpath(root / "data" / "blobs") == os.path.realpath(vol / "data" / "blobs")
