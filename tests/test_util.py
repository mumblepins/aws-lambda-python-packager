# -*- coding: utf-8 -*-
from aws_lambda_python_packager.util import PLATFORMS, get_python_runtime


def to_platform_format(gpr_ret):
    return f"python{gpr_ret[0][0]}.{gpr_ret[0][1]}", gpr_ret[1]


def test_get_python_runtime():
    assert to_platform_format(get_python_runtime()) in PLATFORMS
    assert to_platform_format(get_python_runtime("arm64")) in PLATFORMS
    assert to_platform_format(get_python_runtime("aarch64", (3, 2))) in PLATFORMS
    assert to_platform_format(get_python_runtime(target_version="python3.8")) == ("python3.8", "x86_64")
