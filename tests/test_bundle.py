"""Tests for deploy-bundle auto-ignore: files present at startup that aren't
known runtime state are treated as bundle and excluded from the checkpoint."""

from fractfs.sync import SyncEngine
from conftest import make_config


def test_bundle_files_are_not_checkpointed(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch)
    # the "deployed bundle": files present at first init
    (root / "app.py").write_text("print('hi')")
    (root / "lib").mkdir()
    (root / "lib" / "util.py").write_text("x = 1")

    eng = SyncEngine(cfg)
    bundle = eng.detect_bundle()
    assert "app.py" in bundle
    assert "lib/util.py" in bundle

    # a checkpoint now skips the bundle entirely
    assert eng.checkpoint() == []
    assert not (vol / "_checkpoint" / "app.py").exists()


def test_runtime_files_still_checkpointed_alongside_bundle(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch)
    (root / "app.py").write_text("code")  # bundle

    eng = SyncEngine(cfg)
    eng.detect_bundle()
    # app writes runtime state AFTER init/detection
    (root / "state.db").write_text("runtime")
    copied = eng.checkpoint()
    assert "state.db" in copied
    assert "app.py" not in copied


def test_warm_disk_keeps_runtime_out_of_bundle(layout):
    """On a persistent disk, runtime files already in the checkpoint manifest
    must not be reclassified as bundle on the next detect."""
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch)
    (root / "app.py").write_text("code")  # bundle

    eng = SyncEngine(cfg)
    eng.detect_bundle()
    (root / "state.db").write_text("v1")
    eng.checkpoint()  # state.db now in the manifest

    # simulate a restart on a disk where both files persisted
    eng2 = SyncEngine(cfg)
    bundle = eng2.detect_bundle()
    assert "app.py" in bundle
    assert "state.db" not in bundle  # known runtime, excluded from bundle
    # and it still gets checkpointed (when changed)
    (root / "state.db").write_text("v2-longer-content")
    assert "state.db" in eng2.checkpoint()


def test_redeploy_adds_new_bundle_file(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch)
    (root / "app.py").write_text("v1")
    SyncEngine(cfg).detect_bundle()

    # a new deploy ships an additional file; next cold start re-detects
    (root / "newmod.py").write_text("new")
    eng = SyncEngine(cfg)
    bundle = eng.detect_bundle()
    assert "newmod.py" in bundle
    assert eng.checkpoint() == []


def test_auto_ignore_disabled_checkpoints_everything(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, auto_ignore_bundle=False)
    (root / "app.py").write_text("code")
    eng = SyncEngine(cfg)
    # caller (init) would skip detect_bundle when the flag is off; engine default
    # bundle_paths is empty, so everything is checkpointed
    copied = eng.checkpoint()
    assert "app.py" in copied
