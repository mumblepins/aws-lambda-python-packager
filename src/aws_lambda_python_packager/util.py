# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import re
import sys
from contextlib import contextmanager
from os import PathLike
from typing import Union

import requests

LOG = logging.getLogger(__name__)

_lambda_runtime_regex = re.compile(r"^.*?\|\s*`(python3\.\d+)`\s*\|.*?\|\s*([armx864 \\_,]+)\s*\|.*$")

LAMBDA_RUNTIME_DOCS_URL = (
    "https://raw.githubusercontent.com/awsdocs/aws-lambda-developer-guide/main/doc_source/lambda-runtimes.md"
)

PACKAGE_URL = "https://raw.githubusercontent.com/mumblepins/aws-get-lambda-python-pkg-versions/main/{region}-{python_version}-{architecture}.json"

PathType = Union[str, PathLike]


class ArchitectureUnsupported(Exception):
    """Exception raised when the architecture is not supported"""


def get_lambda_runtimes():
    """Gets a list of supported Lambda runtimes from the AWS docs

    Returns:

    """
    r = requests.get(LAMBDA_RUNTIME_DOCS_URL, timeout=10)
    r.raise_for_status()
    runtimes = []
    for line in r.text.splitlines():
        m = _lambda_runtime_regex.match(line)
        if m:
            runtime = m.group(1)
            archs = m.group(2).split(",")
            archs = [a.strip().replace(r"\_", "_") for a in archs]
            for arch in archs:
                runtimes.append((runtime, arch))
    return runtimes


def check_architecture(architecture):
    """Checks if the given architecture is supported

    Args:
        architecture: Architecture to check

    Returns:
        True if the architecture is supported, False otherwise

    """
    architecture = architecture.lower().strip()
    if architecture == "aarch64":
        architecture = "arm64"
    for _, arch in PLATFORMS:
        if arch == architecture:
            return architecture
    raise ArchitectureUnsupported(f"{architecture} not supported")  # pragma: no cover


def get_python_runtime(architecture="x86_64", target_version=None):
    """Gets an allowed python runtime for the given architecture

    Args:
        architecture: Architecture to target (x86_64 or arm64)
        target_version: Python version to target

    Returns:
        A tuple of (python_version, architecture)

    """
    architecture = check_architecture(architecture)
    if target_version is None:
        py_version = sys.version_info[0:2]
    else:
        if isinstance(target_version, str):
            target_version = target_version.lower().strip("python").strip().split(".")
        py_version = tuple(int(v) for v in target_version)

    filtered_platforms = filter(
        lambda x: x[1] == architecture,
        [(tuple(int(v) for v in pv.strip("python").split(".")), ar) for pv, ar in PLATFORMS],
    )
    allowed_py_version = [a for a, _ in filtered_platforms]
    py_version_constrained = max(min(py_version, max(allowed_py_version)), min(allowed_py_version))
    return py_version_constrained, architecture


@contextmanager
def chdir_cm(path: PathType):
    old_dir = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_dir)


@contextmanager
def chgenv_cm(**kwargs):
    old_env = os.environ.copy()
    os.environ.update({k: v for k, v in kwargs.items() if v is not None})
    for k, v in kwargs.items():
        if v is None:
            if k in os.environ:
                del os.environ[k]

    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_env)


PLATFORMS = get_lambda_runtimes()

__all__ = ["PathType", "PLATFORMS", "chdir_cm", "chgenv_cm"]
