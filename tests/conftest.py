# -*- coding: utf-8 -*-
import shutil
from importlib.resources import files

import pytest


@pytest.fixture(params=["poetry", "pip"])
def temp_path_filled(request, tmp_path):
    print(tmp_path)

    shutil.copytree(files("tests") / "resources" / f"proj_{request.param}", tmp_path / "src", dirs_exist_ok=True)
    return tmp_path / "src", tmp_path / "dst", request.param
