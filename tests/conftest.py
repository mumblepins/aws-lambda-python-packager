# -*- coding: utf-8 -*-
import shutil
from importlib.resources import files

import pytest


@pytest.fixture
def temp_path_filled(tmp_path):
    print(tmp_path)

    shutil.copytree(files("tests") / "resources", tmp_path / "src", dirs_exist_ok=True)
    return tmp_path / "src", tmp_path / "dst"
