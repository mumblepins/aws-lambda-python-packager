# -*- coding: utf-8 -*-
import shutil
import sys

import pytest

if sys.version_info >= (3, 9):
    from importlib.resources import files
else:
    from importlib_resources import files  # type: ignore


@pytest.fixture(params=["poetry", "pip"])
def temp_path_filled(request, tmp_path):
    print(tmp_path)

    shutil.copytree(
        files("tests") / "resources" / f"proj_{request.param}", tmp_path / "src", dirs_exist_ok=True
    )
    return tmp_path / "src", tmp_path / "dst", request.param
