import time

from fractfs.provisioner import provision
from fractfs.sync import SyncEngine, physical_local_path
from conftest import make_config


def test_checkpoint_and_restore_default_files(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch)
    (root / "state").mkdir()
    (root / "state" / "counter.txt").write_text("5")

    eng = SyncEngine(cfg)
    copied = eng.checkpoint()
    assert "state/counter.txt" in copied
    assert (vol / "_checkpoint" / "state" / "counter.txt").read_text() == "5"

    # simulate cold restart: wipe local
    (root / "state" / "counter.txt").unlink()
    restored = eng.restore()
    assert "state/counter.txt" in restored
    assert (root / "state" / "counter.txt").read_text() == "5"


def test_ignored_files_are_not_checkpointed(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, ignore=["*.tmp"])
    (root / "scratch.tmp").write_text("junk")
    (root / "keep.txt").write_text("keep")
    eng = SyncEngine(cfg)
    copied = eng.checkpoint()
    assert "keep.txt" in copied
    assert "scratch.tmp" not in copied
    assert not (vol / "_checkpoint" / "scratch.tmp").exists()


def test_remote_tier_files_not_checkpointed(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"])
    provision(cfg)
    (root / "data" / "blobs" / "big.parquet").write_text("x" * 100)
    (root / "top.txt").write_text("hi")
    eng = SyncEngine(cfg)
    copied = eng.checkpoint()
    assert "top.txt" in copied
    assert "data/blobs/big.parquet" not in copied


def test_unchanged_files_skipped_on_second_checkpoint(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch)
    (root / "a.txt").write_text("hello")
    eng = SyncEngine(cfg)
    assert "a.txt" in eng.checkpoint()
    assert eng.checkpoint() == []  # unchanged -> skipped
    # change it -> picked up again
    time.sleep(0.01)
    (root / "a.txt").write_text("hello world")
    assert "a.txt" in eng.checkpoint()


def test_local_tier_inside_dir_checkpointed_from_scratch(layout):
    root, vol, scratch = layout
    cfg = make_config(
        root, vol, scratch, dirs=["data/blobs"], local=["manifest.json"]
    )
    provision(cfg)
    # writing the manifest creates it on the remote store; pin it
    (vol / "data" / "blobs" / "manifest.json").write_text("m1")
    provision(cfg)  # pins back to scratch
    assert physical_local_path(cfg, "data/blobs/manifest.json") == scratch / "data/blobs/manifest.json"

    eng = SyncEngine(cfg)
    copied = eng.checkpoint()
    assert "data/blobs/manifest.json" in copied
    assert (vol / "_checkpoint" / "data" / "blobs" / "manifest.json").read_text() == "m1"


def test_restore_does_not_clobber_newer_local(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch)
    (root / "a.txt").write_text("old")
    eng = SyncEngine(cfg)
    eng.checkpoint()
    (root / "a.txt").write_text("new-local")
    restored = eng.restore()  # default: don't overwrite
    assert "a.txt" not in restored
    assert (root / "a.txt").read_text() == "new-local"
