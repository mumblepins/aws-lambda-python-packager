# -*- coding: utf-8 -*-
import logging
import os
import re
import sys
from contextlib import contextmanager
from io import StringIO
from os import PathLike
from typing import TextIO

import requests
from cleo.io.outputs.output import Verbosity
from cleo.io.outputs.stream_output import StreamOutput

LOG = logging.getLogger(__name__)

_lambda_runtime_regex = re.compile(r"^.*?\|\s*`(python3\.\d+)`\s*\|.*?\|\s*([armx864 \\_,]+)\s*\|.*$")

LAMBDA_RUNTIME_DOCS_URL = (
    "https://raw.githubusercontent.com/awsdocs/aws-lambda-developer-guide/main/doc_source/lambda-runtimes.md"
)


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
def chdir(path: PathLike):
    old_dir = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_dir)


@contextmanager
def chgenv(**kwargs):
    old_env = os.environ.copy()
    os.environ.update(kwargs)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_env)


@contextmanager
def chg_argv(args: list):
    old_argv = list(sys.argv)
    sys.argv = args
    try:
        yield
    finally:
        sys.argv = old_argv


PACKAGE_URL = "https://raw.githubusercontent.com/mumblepins/aws-get-lambda-python-pkg-versions/main/{region}-{python_version}-{architecture}.json"


def get_packages_to_ignore(region: str, architecture: str, python_version: str) -> tuple[list[str], dict[str, str]]:
    try:
        r = requests.get(PACKAGE_URL.format(region=region, architecture=architecture, python_version=python_version))
        r.raise_for_status()
        data = r.json()
        pkgs_to_ignore = [f"{k}=={v}" for k, v in data.items()]
        pkgs_to_ignore_dict = data
    except Exception as e:
        LOG.warning(f"Failed to get packages to ignore: {e}", exc_info=True)
        pkgs_to_ignore = []
        pkgs_to_ignore_dict = {}
    return pkgs_to_ignore, pkgs_to_ignore_dict


class BufferedStreamOutput(StreamOutput):
    def _write(self, message: str, new_line: bool = False) -> None:
        super()._write(message, new_line)
        self._buffer.write(message)

        if new_line:
            self._buffer.write("\n")

    def __init__(self, stream: TextIO, verbosity: Verbosity = Verbosity.NORMAL) -> None:
        super().__init__(stream, verbosity, decorated=False, formatter=None)
        self._buffer = StringIO()

    def fetch(self) -> str:
        """
        Empties the buffer and returns its content.
        """
        content = self._buffer.getvalue()
        self._buffer = StringIO()

        return content
