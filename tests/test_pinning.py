"""Tests for always-local pinning: pre-created (dangling) back-symlinks for
concrete patterns, directory back-symlinks, and the glob warning."""


from fractfs.provisioner import provision, warnings_for
from fractfs.patterns import is_anchored, is_concrete, is_glob, pin_targets
from fractfs.resolver import Tier, resolve
from conftest import make_config


# -- pattern analysis ------------------------------------------------------


def test_pattern_classification():
    assert is_glob("*.lock")
    assert is_glob("data/[ab].txt")
    assert not is_glob("manifest.json")
    assert is_concrete("manifest.json")
    assert is_concrete("data/blobs/manifest.json")
    assert not is_concrete("*.lock")
    assert not is_concrete("!keep.txt")
    assert is_anchored("data/blobs/manifest.json")
    assert not is_anchored("manifest.json")
    assert not is_anchored("manifest.json/")  # trailing slash only -> unanchored


def test_pin_targets_unanchored_hits_every_dir():
    targets = pin_targets("manifest.json", ["data/blobs", "exports"])
    assert ("data/blobs/manifest.json", False) in targets
    assert ("exports/manifest.json", False) in targets


def test_pin_targets_anchored_only_if_inside_a_dir():
    assert pin_targets("data/blobs/manifest.json", ["data/blobs"]) == [
        ("data/blobs/manifest.json", False)
    ]
    assert pin_targets("other/manifest.json", ["data/blobs"]) == []


def test_pin_targets_directory_pattern():
    assert pin_targets(".locks/", ["data/blobs"]) == [("data/blobs/.locks", True)]


def test_glob_yields_no_targets():
    assert pin_targets("*.lock", ["data/blobs"]) == []


# -- dangling file back-symlink: local from the FIRST write ----------------


def test_concrete_local_file_is_local_from_first_write(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"], local=["manifest.json"])
    provision(cfg)

    link = vol / "data" / "blobs" / "manifest.json"
    assert link.is_symlink()  # pre-created, dangling
    assert not link.exists()  # nothing written yet

    # First write through the app path lands in scratch, never on the volume.
    (root / "data" / "blobs" / "manifest.json").write_text("v1")
    assert (scratch / "data" / "blobs" / "manifest.json").read_text() == "v1"
    assert link.is_symlink()  # still a link, not a real file on the volume


def test_concrete_pin_is_idempotent(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"], local=["manifest.json"])
    provision(cfg)
    actions = provision(cfg)
    assert any("pinned local (already)" in a for a in actions)


# -- directory back-symlink: arbitrary names always local ------------------


def test_directory_pin_keeps_arbitrary_names_local(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"], ignore=[".locks/"])
    provision(cfg)

    locks_link = vol / "data" / "blobs" / ".locks"
    assert locks_link.is_symlink()

    # Arbitrarily-named lock files created later all land local.
    (root / "data" / "blobs" / ".locks" / "abc123.lock").write_text("1")
    (root / "data" / "blobs" / ".locks" / "9f8e.pid").write_text("2")
    assert (scratch / "data" / "blobs" / ".locks" / "abc123.lock").read_text() == "1"
    assert (scratch / "data" / "blobs" / ".locks" / "9f8e.pid").read_text() == "2"
    # and they resolve to the ignore tier (not checkpointed)
    assert resolve("data/blobs/.locks/abc123.lock", cfg) == Tier.LOCAL_IGNORED


def test_directory_pin_migrates_existing_volume_data(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"], local=["state/"])
    provision(cfg)  # creates the dir symlink + pins state/
    # simulate data that previously landed on the volume under state/
    (vol / "data" / "blobs" / "state").mkdir(parents=True, exist_ok=True)
    # break the existing pin to mimic a pre-pin layout, then re-provision
    real = vol / "data" / "blobs" / "state"
    if real.is_symlink():
        real.unlink()
    real.mkdir(parents=True, exist_ok=True)
    (real / "old.dat").write_text("legacy")
    provision(cfg)
    assert (vol / "data" / "blobs" / "state").is_symlink()
    assert (scratch / "data" / "blobs" / "state" / "old.dat").read_text() == "legacy"


# -- the boundary warning --------------------------------------------------


def test_local_glob_inside_dirs_warns(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"], local=["*.lock"])
    warns = warnings_for(cfg)
    assert any("*.lock" in w and "glob" in w for w in warns)


def test_no_warning_without_dirs(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, local=["*.lock"])
    assert warnings_for(cfg) == []


def test_no_warning_for_concrete_local(layout):
    root, vol, scratch = layout
    cfg = make_config(root, vol, scratch, dirs=["data/blobs"], local=["manifest.json"])
    assert warnings_for(cfg) == []
