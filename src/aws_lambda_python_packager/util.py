# -*- coding: utf-8 -*-
import logging
import os
import re
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


def get_lambda_runtimes():
    """Gets a list of supported Lambda runtimes from the AWS docs

    Returns:

    """
    r = requests.get(LAMBDA_RUNTIME_DOCS_URL)
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


@contextmanager
def chdir_cm(path: Union[str, PathLike]):
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
