# -*- coding: utf-8 -*-
import os
from pathlib import Path

import toml

from aws_lambda_python_packager.lambda_packager import LambdaPackager

resources = Path(os.path.realpath(__file__)).parent / "resources"


def load_pyproject(folder):
    pyproject = folder / "pyproject.toml"
    with pyproject.open() as f:
        data = toml.load(f)
    return data


def test_export_ignore_packages_update_pyproject(temp_path_filled):
    src, dst = temp_path_filled

    lp = LambdaPackager(src, update_pyproject=True, ignore_packages=True)
    lp.package(dst)
    aw = dst / "awswrangler"
    pkg = dst / "test_package"
    assert aw.exists() and aw.is_dir()
    assert pkg.exists() and pkg.is_dir()
    assert not (dst / "boto3").exists()
    assert not (dst / "botocore").exists()

    # Test that the pyproject.toml is updated
    data = load_pyproject(src)
    assert data["tool"]["poetry"]["dependencies"]["urllib3"] == "1.26.6"


def test_export_ignore_packages_no_update_pyproject(temp_path_filled):
    src, dst = temp_path_filled

    lp = LambdaPackager(src, update_pyproject=False, ignore_packages=True)
    lp.package(dst)
    aw = dst / "awswrangler"
    pkg = dst / "test_package"
    assert aw.exists() and aw.is_dir()
    assert pkg.exists() and pkg.is_dir()
    assert not (dst / "boto3").exists()

    assert (dst / "botocore").exists()

    data = load_pyproject(src)
    assert data["tool"]["poetry"]["dependencies"]["boto3"] == "1.20.32"
    assert "urllib3" not in data["tool"]["poetry"]["dependencies"]


def test_export_no_ignore_packages_no_update_pyproject(temp_path_filled):
    src, dst = temp_path_filled

    lp = LambdaPackager(src, update_pyproject=False, ignore_packages=False)
    lp.package(dst)
    aw = dst / "awswrangler"
    pkg = dst / "test_package"
    assert aw.exists() and aw.is_dir()
    assert pkg.exists() and pkg.is_dir()
    assert (dst / "boto3").exists()

    assert (dst / "botocore").exists()

    data = load_pyproject(src)
    assert data["tool"]["poetry"]["dependencies"]["boto3"] == "1.20.32"
    assert "urllib3" not in data["tool"]["poetry"]["dependencies"]


def test_arm64(temp_path_filled):
    src, dst = temp_path_filled
    lp = LambdaPackager(src, update_pyproject=False, ignore_packages=False, architecture="arm64")
    lp.package(dst)
    numpy_file = list(dst.glob("numpy*dist-info/WHEEL"))[0]
    assert "manylinux2014_aarch64" in numpy_file.read_text()
