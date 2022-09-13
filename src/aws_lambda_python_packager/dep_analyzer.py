# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess  # nosec
import tempfile
from abc import ABC, abstractmethod
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Union,
)

import requests

from .util import PathType, chdir_cm

PackageInfo = namedtuple("PackageInfo", ["name", "version", "version_spec"])
PACKAGE_URL = "https://raw.githubusercontent.com/mumblepins/aws-get-lambda-python-pkg-versions/main/{region}-python{python_version}-{architecture}.json"


class CommandNotFoundError(Exception):
    pass


class ExtraLine(list):
    pass


class DepAnalyzer(ABC):  # pylint: disable=too-many-instance-attributes

    project_root: Path

    analyzer_name: str

    # region init and teardown
    def __init__(
        self,
        project_root: PathType | None,
        python_version: str = "3.9",
        architecture: str = "x86_64",
        region: str = "us-east-1",
        ignore_packages=False,
        update_dependencies=False,
    ):
        self._extra_lines: Optional[List[ExtraLine]] = None
        self._exported_reqs = None
        self._reqs: Optional[Dict[Any, PackageInfo]] = None
        self._pkgs_to_ignore_dict = None
        if project_root is None:
            self.project_root = Path.cwd()
        else:
            self.project_root = Path(project_root)

        self._pip = shutil.which("pip")
        if self._pip is None:
            raise CommandNotFoundError("pip not found, please install and add to PATH")
        self.python_version = python_version
        self.architecture = architecture
        self.region = region

        self.ignore_packages = ignore_packages
        self.update_dependencies = update_dependencies
        self._temp_proj_dir = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self._chdir = partial(chdir_cm, self._temp_proj_dir.name)
        self._target = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with

        self.log = logging.getLogger(self.__class__.__name__)

    def __del__(self):
        try:
            self._target.cleanup()
            self._temp_proj_dir.cleanup()
        except OSError:
            pass

    # endregion

    # region abstract methods
    @abstractmethod
    def _get_requirements(self) -> Iterable[Union[PackageInfo, ExtraLine]]:
        pass

    @abstractmethod
    def _update_dependency_file(self, pkgs_to_add: Dict[str, PackageInfo]):
        pass

    @abstractmethod
    def direct_dependencies(self) -> Dict[str, str]:
        pass

    # endregion

    # region properties
    @property
    def pkgs_to_ignore_dict(self):
        if not self.ignore_packages:
            return {}
        if self._pkgs_to_ignore_dict is None:
            self._pkgs_to_ignore_dict = self._get_packages_to_ignore()
        return self._pkgs_to_ignore_dict

    @property
    def pkgs_to_ignore(self):
        return [f"{k}=={v}" for k, v in self.pkgs_to_ignore_dict.items()]

    @property
    def pkgs_to_ignore_info(self):
        return {k: PackageInfo(k, v, f"{k}=={v}") for k, v in self.pkgs_to_ignore_dict.items()}

    @property
    def requirements(self) -> Dict[str, PackageInfo]:
        if self._reqs is None:
            self.log.warning("Exporting requirements")
            reqs = self.update_dependency_file()
            if reqs is None:
                reqs = list(self.get_requirements())
            if self._extra_lines is None:
                self._extra_lines = [r for r in reqs if isinstance(r, ExtraLine)]
            self._reqs = {r.name: r for r in reqs if not isinstance(r, ExtraLine)}
        return self._reqs

    @property
    def extra_lines(self):
        if self._extra_lines is None:
            _ = self.requirements
        return self._extra_lines

    # endregion

    # region private methods
    @contextmanager
    def _change_context(self):
        with self._chdir():
            yield

    def _log_popen_output(self, output, level=logging.DEBUG, prefix=""):
        data = ""
        for line in output:
            o = line.decode("utf-8")
            self.log.log(level, prefix + o.rstrip())
            data += o
        return data

    def _install_pip(self, *args, return_state=False, quiet=False, requirements_file=False):
        pip_command = [
            "install",
            "--disable-pip-version-check",
            "--ignore-installed",
            "--no-compile",
            "--python-version",
            self.python_version,
            "--implementation",
            "cp",
        ]
        if not requirements_file:
            for el in self.extra_lines:
                pip_command.extend(el)
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
        pip_command.extend(args)
        return self.run_pip(*pip_command, return_state=return_state, quiet=quiet)

    def _get_packages_to_ignore(self):
        try:
            r = requests.get(
                PACKAGE_URL.format(
                    region=self.region, architecture=self.architecture, python_version=self.python_version
                ),
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            pkgs_to_ignore_dict = data
        except Exception as e:  # pylint: disable=broad-except
            self.log.warning("Failed to get packages to ignore: %s", e, exc_info=True)
            pkgs_to_ignore_dict = {}
        return pkgs_to_ignore_dict

    # endregion

    # region public methods
    def get_requirements(self) -> Iterable[Union[PackageInfo, ExtraLine]]:
        self.log.info("Getting requirements info using %s", self.analyzer_name)
        return self._get_requirements()

    @classmethod
    def process_requirements(cls, requirements: Iterable[str]) -> Iterable[Union[PackageInfo, ExtraLine]]:
        for line in requirements:
            if line.startswith("#") or line.strip() == "":
                continue
            if line.startswith("-"):
                yield ExtraLine(shlex.split(line))
                continue
            pkg_match = re.match(r"^([^= \n]*)(==)?([^\s;]*).*$", line)
            if pkg_match:
                pkg_name, _, pkg_version = pkg_match.groups()

                yield PackageInfo(pkg_name, pkg_version, line.rstrip())

    def run_command(
        self, *args, return_state=False, quiet=False, prefix=None, context=None
    ) -> Union[bool, Tuple[str, str]]:
        if prefix is None:
            prefix = Path(args[0]).name
        self.log.debug("Running command: %s", args)
        if context is None:
            context = self._change_context
        if quiet:
            loglevel = logging.DEBUG
        else:
            loglevel = logging.INFO
        with context():
            with subprocess.Popen(  # nosec
                [str(a) for a in args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as proc:
                with ThreadPoolExecutor(2) as pool:
                    stdout_fut = pool.submit(
                        self._log_popen_output,
                        proc.stdout,
                        loglevel,
                        prefix + "(OUT)>  ",
                    )
                    stderr_fut = pool.submit(
                        self._log_popen_output,
                        proc.stderr,
                        loglevel,
                        prefix + "(ERR)>  ",
                    )
                    stdout, stderr = stdout_fut.result(), stderr_fut.result()
                proc.wait()
                if return_state:
                    return not bool(proc.returncode)
                if proc.returncode:
                    self.log.error("ERROR IN CALL: %s", args)
                    self.log.error("STDOUT: %s", stdout)
                    self.log.error("STDERR: %s", stderr)
                    raise subprocess.CalledProcessError(proc.returncode, args, stdout, stderr)

                return stdout, stderr

    def update_dependency_file(self):
        if not self.ignore_packages or not self.update_dependencies:
            return None
        self.log.info(
            "Checking to see if any dependencies need to be changed in the dependency file to match the AWS Lambda environment"
        )
        cur_requires = list(self.get_requirements())
        pkgs_to_add = {}
        for pkg in cur_requires:
            if isinstance(pkg, ExtraLine):
                continue
            pkg_name, pkg_version, _ = pkg
            if pkg_name in self.pkgs_to_ignore_dict and pkg_version != self.pkgs_to_ignore_dict[pkg_name]:
                self.log.warning(
                    "%s is currently %s but should be %s", pkg_name, pkg_version, self.pkgs_to_ignore_dict[pkg_name]
                )
                pkgs_to_add[pkg_name] = self.pkgs_to_ignore_info[pkg_name]
        if len(pkgs_to_add) > 0:
            self.log.info("Updating dependency file to add %s requirements", len(pkgs_to_add))
            self._update_dependency_file(pkgs_to_add)
            return None
        self.log.info("No changes needed in the dependency file")
        return cur_requires

    def export_requirements(self):
        if self._exported_reqs is None:
            output = []
            for pkg_name, pkg_version, pkg_spec in self.requirements.values():
                if self.ignore_packages and self.pkgs_to_ignore_dict.get(pkg_name, None) == pkg_version:
                    self.log.warning(
                        "Ignoring %s as it should be in the AWS Lambda Environment already",
                        pkg_spec.strip().split(";")[0],
                    )
                    continue
                output.append(pkg_spec)
            self._exported_reqs = output
        return self._exported_reqs

    def run_pip(self, *args, return_state=False, quiet=False, context=None):
        self.run_command(self._pip, *args, return_state=return_state, quiet=quiet, context=context)

    def install_dependencies(self, quiet=True):
        pip_command = [
            "--target",
            self._target.name,
            "--no-deps",
        ]
        pip_command.extend(self.export_requirements())
        self.log.warning("Installing dependencies using pip")
        self._install_pip(*pip_command, quiet=quiet)
        self.log.warning("Installing dependencies done")

    def install_root(self):
        src_path = self.project_root / "src"
        if src_path.exists():
            if (src_path / "__init__.py").exists():
                self.log.warning("src/__init__.py exists, installing as package in target")
                shutil.copytree(src_path, Path(self._target.name) / "src")
            else:
                self.log.warning("src/__init__.py does not exist, installing files from src directly into target")
                shutil.copytree(src_path, Path(self._target.name), dirs_exist_ok=True)
        elif next(self.project_root.glob("*.py"), None):
            for f in self.project_root.glob("*.py"):
                self.log.warning("Copying %s to target", f)
                shutil.copy(f, self._target.name)
        else:
            self.log.warning("No src/__init__.py or *.py files found, no root program is being installed")

    def get_layer_files(self):
        target_path = Path(self._target.name)
        return [a.relative_to(target_path) for a in target_path.iterdir()]

    def copy_from_target(self, dst: PathType):
        self.log.warning("Copying %s from target to %s", self._target.name, dst)
        shutil.copytree(self._target.name, dst)

    def copy_from_temp_dir(self, files: Iterable[str]):
        for f in files:
            fp = Path(self._temp_proj_dir.name) / f
            if fp.exists():
                shutil.copy(fp, self.project_root)

    def copy_to_temp_dir(self, files: Iterable[str]):
        for f in files:
            fp = Path(self.project_root) / f
            if fp.exists():
                shutil.copy(fp, self._temp_proj_dir.name)

    def backup_files(self, files: Iterable[str]):
        date_str = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
        for f in files:
            fp = Path(self.project_root) / f
            if fp.exists():
                shutil.copy(fp, fp.with_suffix(f".{date_str}{fp.suffix}"))

    # endregion
