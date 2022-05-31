# -*- coding: utf-8 -*-
import os
from pathlib import Path

from aws_lambda_python_packager import main_args
from aws_lambda_python_packager.lambda_packager import LambdaPackager

resources = Path(os.path.realpath(__file__)).parent / "resources"

pkg_type_map = {"poetry": "test_package", "pip": "test_app.py"}


def test_export_ignore_packages_update_reqs(temp_path_filled):
    src, dst, pkg_type = temp_path_filled

    lp = LambdaPackager(src, dst, update_dependencies=True, ignore_packages=True)
    lp.package()
    aw = dst / "awswrangler"

    pkg = dst / pkg_type_map[pkg_type]
    assert aw.exists() and aw.is_dir()
    assert pkg.exists()
    assert not (dst / "boto3").exists()
    assert not (dst / "botocore").exists()

    d_deps = lp.analyzer.direct_dependencies()
    assert d_deps["urllib3"] == "1.26.6"


def test_export_ignore_packages_no_update_pyproject(temp_path_filled):
    src, dst, pkg_type = temp_path_filled
    lp = LambdaPackager(src, dst, update_dependencies=False, ignore_packages=True)
    lp.package()
    aw = dst / "awswrangler"
    pkg = dst / pkg_type_map[pkg_type]
    assert aw.exists() and aw.is_dir()
    assert pkg.exists()
    assert not (dst / "boto3").exists()

    assert (dst / "botocore").exists()

    d_deps = lp.analyzer.direct_dependencies()
    assert d_deps["boto3"] == "1.20.32"
    assert "urllib3" not in d_deps


def test_export_no_ignore_packages_no_update_pyproject(temp_path_filled):
    src, dst, pkg_type = temp_path_filled

    lp = LambdaPackager(src, dst, update_dependencies=False, ignore_packages=False)
    lp.package()
    aw = dst / "awswrangler"
    pkg = dst / pkg_type_map[pkg_type]
    assert aw.exists() and aw.is_dir()
    assert pkg.exists()
    assert (dst / "boto3").exists()

    assert (dst / "botocore").exists()

    d_deps = lp.analyzer.direct_dependencies()
    assert d_deps["boto3"] == "1.20.32"
    assert "urllib3" not in d_deps


def test_arm64(temp_path_filled):
    src, dst, _ = temp_path_filled
    lp = LambdaPackager(src, dst, update_dependencies=False, ignore_packages=False, architecture="arm64")
    lp.package()
    numpy_file = list(dst.glob("numpy*dist-info/WHEEL"))[0]
    assert "manylinux2014_aarch64" in numpy_file.read_text()


def test_full_optimize(temp_path_filled):
    src, dst, _ = temp_path_filled
    main_args([str(a) for a in ["-v", "-O", src, dst]])
    pyarrow_file = list(dst.glob("**/pyarrow/**/libarrow.so.*"))[0]
    assert 32_000_000 < os.path.getsize(pyarrow_file) < 40_000_000
    # make sure we aren't using the aws wrangler version
    assert list(dst.glob("**/pyarrow/**/libarrow_flight.so.*"))[0].exists()


def test_full_optimize_w_awswrangler(temp_path_filled):
    src, dst, _ = temp_path_filled
    main_args([str(a) for a in ["-v", "-O", "--use-aws-pyarrow", src, dst]])
    pyarrow_file = list(dst.glob("**/pyarrow/**/libarrow.so.*"))[0]
    assert os.path.getsize(pyarrow_file) < 32_000_000
    # make sure we are using the aws wrangler version
    assert len(list(dst.glob("**/pyarrow/**/libarrow_flight.so.*"))) == 0


def test_optimize_all_arm(temp_path_filled):
    # TODO: improve tests for optimization
    src, dst, _ = temp_path_filled
    zip_file = src.parent / "test_package.zip"
    main_args([str(a) for a in ["-v", "-OO", "-z", zip_file, "--architecture", "arm64", src, dst]])

    numpy_file = list(dst.glob("numpy*dist-info/WHEEL"))[0]
    assert "manylinux2014_aarch64" in numpy_file.read_text()
    assert zip_file.exists() and zip_file.stat().st_size > 1 * 1024 * 1024
