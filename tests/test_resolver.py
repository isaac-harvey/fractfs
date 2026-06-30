from fractfs.resolver import Tier, resolve
from conftest import make_config


def cfg(tmp_path, **kw):
    return make_config(tmp_path / "app", tmp_path / "vol", tmp_path / "scratch", **kw)


def test_default_is_local_synced(tmp_path):
    c = cfg(tmp_path)
    assert resolve("foo/bar.txt", c) == Tier.LOCAL_SYNCED


def test_dirs_redirect_to_volume(tmp_path):
    c = cfg(tmp_path, dirs=["data/blobs"])
    assert resolve("data/blobs/x.parquet", c) == Tier.VOLUME
    assert resolve("data/blobs", c) == Tier.VOLUME
    assert resolve("data/blobs/nested/y.parquet", c) == Tier.VOLUME
    assert resolve("data/other.txt", c) == Tier.LOCAL_SYNCED


def test_ignore_beats_everything(tmp_path):
    c = cfg(tmp_path, dirs=["data/blobs"], ignore=["*.tmp"], local=["*.tmp"])
    assert resolve("data/blobs/scratch.tmp", c) == Tier.LOCAL_IGNORED


def test_local_overrides_dirs(tmp_path):
    c = cfg(tmp_path, dirs=["data/blobs"], local=["manifest.json"])
    assert resolve("data/blobs/manifest.json", c) == Tier.LOCAL_SYNCED
    # but a sibling blob still goes to the volume
    assert resolve("data/blobs/big.parquet", c) == Tier.VOLUME


def test_anchored_pattern_matches_only_there(tmp_path):
    c = cfg(tmp_path, dirs=["data/blobs"], local=["data/blobs/manifest.json"])
    assert resolve("data/blobs/manifest.json", c) == Tier.LOCAL_SYNCED
    assert resolve("other/manifest.json", c) == Tier.LOCAL_SYNCED  # default anyway
    assert resolve("data/blobs/manifest.json".replace("blobs", "other"), c) == Tier.LOCAL_SYNCED


def test_leading_dotslash_and_absolute_normalized(tmp_path):
    c = cfg(tmp_path, dirs=["data/blobs"])
    assert resolve("./data/blobs/x", c) == Tier.VOLUME
    assert resolve("/data/blobs/x", c) == Tier.VOLUME
