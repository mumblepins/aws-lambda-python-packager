# -*- coding: utf-8 -*-
"""
AWS Lambda Packager

This script is used to package the lambda function code into a zip file.
It is an alternative to `sam build` and uses poetry to manage dependencies.

"""
import io
import logging
import os
import re
import subprocess  # nosec B404
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from os import PathLike
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import (
    IO,
    List,
    Optional,
    Union,
)

import requests
from poetry.core.masonry.api import build_sdist

LOG = logging.getLogger(__name__)
PLATFORMS = [
    ("x86_64", "python3.7"),
    ("x86_64", "python3.9"),
    ("x86_64", "python3.8"),
    ("arm64", "python3.9"),
    ("arm64", "python3.8"),
]

# From https://github.com/mumblepins/aws-get-lambda-python-pkg-versions
PACKAGE_URL = "https://raw.githubusercontent.com/mumblepins/aws-get-lambda-python-pkg-versions/main/{region}-{python_version}-{architecture}.json"


def get_packages_to_ignore(region: str, architecture: str, python_version: str) -> tuple[list[str], dict[str, str]]:
    r = requests.get(PACKAGE_URL.format(region=region, architecture=architecture, python_version=python_version))
    r.raise_for_status()
    data = r.json()
    pkgs_to_ignore = [f"{k}=={v}" for k, v in data.items()]
    pkgs_to_ignore_dict = data
    return pkgs_to_ignore, pkgs_to_ignore_dict


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


class LambdaPackager:

    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        pyproject_path: Union[str, PathLike],
        python_version: str = "python3.9",
        architecture: str = "x86_64",
        region: str = "us-east-1",
        update_pyproject: bool = False,
        ignore_packages: bool = False,
    ):  # pylint: disable=too-many-arguments
        """Initialize the Lambda Packager

        Args:
            pyproject_path: Path to the pyproject.toml file
            python_version: Python version to target
            architecture: Architecture to target (x86_64 or arm64)
            region: AWS region to target
            update_pyproject: whether to update pyproject.toml with the appropriate versions of packages
                from the AWS lambda environment (ignored if ignore_packages is False)
            ignore_packages: Ignore packages that already exist in the AWS lambda environment
        """
        if re.match(r"^\d+\.\d+$", python_version):
            python_version = f"python{python_version}"
        if (architecture, python_version) not in PLATFORMS:
            raise Exception(f"{architecture} {python_version} not supported")  # pragma: no cover
        self.pyproject_path = Path(pyproject_path)
        self.python_version = python_version
        self.architecture = architecture
        self.region = region
        self.update_pyproject = update_pyproject
        self.ignore_packages = ignore_packages
        self.packages_to_ignore, self.packages_to_ignore_dict = get_packages_to_ignore(region, architecture, python_version)
        self._chngenv = partial(chgenv, POETRY_VIRTUALENVS_CREATE="false")
        self._chdir = partial(chdir, self.pyproject_path)

    def _run_cmd(self, *args, **kwargs):
        _kwargs = {
            "check": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        _kwargs.update(kwargs)

        with self._chngenv(), self._chdir():
            r = subprocess.run(*args, **_kwargs)  # noqa: pylint: disable=subprocess-run-check # nosec: B603
        if r.stdout is not None:
            return r.stdout.decode("utf-8"), r

        return r

    def _export_requirements(self):
        # noinspection PyTypeChecker
        reqs, _ = self._run_cmd(
            [
                "poetry",
                "export",
                "--format=requirements.txt",
                "--without-hashes",
            ]
        )
        return reqs

    def _lock(self):
        # noinspection PyTypeChecker
        return self._run_cmd(["poetry", "lock", "--no-update"])

    def _locked(self):
        # noinspection PyTypeChecker
        _, r = self._run_cmd(["poetry", "lock", "--check", "-q"], check=False)
        return not bool(r.returncode)

    def _change_pyproject(self):
        cur_requires = self._export_requirements()
        pkgs_to_add_to_pyproject = {}
        for line in cur_requires.splitlines(True):
            pkg_name, pkg_version = re.match(r"^([^= ]*?)==(\S*).*$", line).groups()
            if pkg_name in self.packages_to_ignore_dict and pkg_version != self.packages_to_ignore_dict[pkg_name]:
                print(
                    f"{pkg_name} is currently {pkg_version} but should be {self.packages_to_ignore_dict[pkg_name]}",
                    file=sys.stderr,
                )

                pkgs_to_add_to_pyproject[pkg_name] = self.packages_to_ignore_dict[pkg_name]
        if len(pkgs_to_add_to_pyproject) > 0:
            Path(self.pyproject_path, f"pyproject.{datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%SZ')}.toml").write_text(
                Path(self.pyproject_path, "pyproject.toml").read_text(encoding="utf8"),
                encoding="utf8",
            )
            self._run_cmd(
                [
                    "poetry",
                    "add",
                    "--lock",
                    *[f"{k}=={v}" for k, v in pkgs_to_add_to_pyproject.items()],
                ]
            )

    def export_reqs(
        self,
        output: Optional[Union[io.StringIO, IO[str]]] = None,
    ):
        """

        :param output:
        :return:
        """
        _output: Union[io.StringIO, IO[str]] = output or io.StringIO()

        if not self._locked():
            LOG.info("not locked, locking")
            self._lock()
        if self.ignore_packages and self.update_pyproject:
            self._change_pyproject()
        reqs = self._export_requirements()

        for line in reqs.splitlines(True):
            if self.ignore_packages and re.sub(r"^([^= ]*?==\S*).*$", r"\1", line).strip() in self.packages_to_ignore:
                LOG.debug("ignoring %s", line.strip())
                continue
            _output.write(line)
        if output is None:
            return _output.getvalue()  # type: ignore
        return None

    @contextmanager
    def build_sdist(self):
        sdist_location = tempfile.TemporaryDirectory()
        try:
            with self._chdir(), self._chngenv():
                sdist_name = build_sdist(sdist_location.name)
            yield Path(sdist_location.name) / sdist_name
        finally:
            sdist_location.cleanup()

    def _add_architecture(self, pip_command: List[str]):
        if self.architecture == "arm64":
            pip_command.extend(
                [
                    "--platform",
                    "manylinux2014_aarch64",
                ]
            )
        elif self.architecture == "x86_64":
            pip_command.extend(
                [
                    "--platform",
                    "manylinux2014_x86_64",
                ]
            )

        return pip_command

    def package_depends(self, output_dir: Union[str, PathLike]):
        reqs = NamedTemporaryFile("w+t", encoding="utf8", delete=False)  # noqa: pylint: disable=consider-using-with
        self.export_reqs(reqs.file)
        reqs.close()
        output_dir = Path(output_dir)
        pip_install_command = [
            "pip",
            "install",
            "-r",
            reqs.name,
            "--target",
            str(output_dir.absolute()),
            "--no-deps",
            "--disable-pip-version-check",
            "--ignore-installed",
            "--python-version",
            self.python_version.replace("python", ""),
            "--implementation",
            "cp",
        ]
        pip_install_command = self._add_architecture(pip_install_command)
        # noinspection PyTypeChecker
        self._run_cmd(
            pip_install_command,
            stdout=None,
            stderr=None,
        )
        os.remove(reqs.name)

    def package_main(self, output_dir: PathLike):
        with self.build_sdist() as sdist_path:
            pip_install_command = [
                "pip",
                "install",
                "--no-deps",
                "--ignore-installed",
                "--disable-pip-version-check",
                "--target",
                output_dir,
                sdist_path,
            ]
            pip_install_command = self._add_architecture(pip_install_command)
            self._run_cmd(pip_install_command, stdout=None, stderr=None)

    def package(self, output_dir: PathLike):
        self.package_depends(output_dir)
        self.package_main(output_dir)
