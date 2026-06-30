import pytest

from fractfs.config import Config


def make_config(root, volume_root, scratch, *, dirs=(), ignore=(), local=(), **kw):
    return Config(
        root=root,
        backend="volumes",
        volume_root=volume_root,
        scratch=scratch,
        dir_paths=list(dirs),
        ignore_patterns=list(ignore),
        local_patterns=list(local),
        **kw,
    )


@pytest.fixture
def layout(tmp_path):
    """A throwaway app root / volume / scratch trio."""
    root = tmp_path / "app"
    vol = tmp_path / "vol"
    scratch = tmp_path / "scratch"
    root.mkdir()
    vol.mkdir()
    scratch.mkdir()
    return root, vol, scratch
