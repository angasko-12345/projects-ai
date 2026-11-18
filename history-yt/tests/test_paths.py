import os

import pytest

from histyt.config import ConfigError, ensure_d_confined, get_project_root


def test_project_dir_is_allowed():
    assert ensure_d_confined(get_project_root())


@pytest.mark.skipif(os.name != "nt", reason="drive letters are Windows-only")
def test_c_drive_is_rejected():
    with pytest.raises(ConfigError, match="outside D:"):
        ensure_d_confined("C:/Windows/System32")


@pytest.mark.skipif(os.name != "nt", reason="drive letters are Windows-only")
def test_d_drive_is_allowed():
    assert ensure_d_confined("D:/tmp")
