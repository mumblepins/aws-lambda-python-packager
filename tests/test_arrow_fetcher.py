import pytest

from aws_lambda_python_packager.arrow_fetcher import fetch_arrow_package


def test_fetch_arrow_package(tmp_path):
    fetch_arrow_package(tmp_path, "10.0.1", "3.9", "arm64")
    assert (tmp_path / "pyarrow/compute.py").exists()


def test_fetch_arrow_package_exception(tmp_path):
    with pytest.raises(ValueError):
        fetch_arrow_package(tmp_path, "10.0.1a", "3.9", "arm64")
