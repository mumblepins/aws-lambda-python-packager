# -*- coding: utf-8 -*-
"""
AWS Lambda Packager

This script is used to package the lambda function code into a zip file.
It is an alternative to `sam build` and uses poetry to manage dependencies.

"""
import logging
import os
import platform
import shutil
import subprocess  # nosec B404
from compileall import compile_dir
from os import PathLike
from pathlib import Path
from typing import Type, Union
from zipfile import ZIP_DEFLATED, ZipFile

from .aws_wrangler import fetch_package
from .dep_analyzer import DepAnalyzer
from .pip_analyzer import PipAnalyzer
from .poetry_analyzer import PoetryAnalyzer
from .util import PLATFORMS

LOG = logging.getLogger(__name__)
MAX_LAMBDA_SIZE = 250 * 1024 * 1024  # 250MB


class LambdaPackager:
    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        project_path: Union[str, PathLike],
        output_dir: Union[str, PathLike],
        python_version: str = "3.9",
        architecture: str = "x86_64",
        region: str = "us-east-1",
        update_dependencies: bool = False,
        ignore_packages: bool = False,
    ):  # pylint: disable=too-many-arguments
        """Initialize the Lambda Packager

        Args:
            project_path: Path to the pyproject.toml file
            python_version: Python version to target
            architecture: Architecture to target (x86_64 or arm64)
            region: AWS region to target
            update_dependencies: whether to update pyproject.toml with the appropriate versions of packages
                from the AWS lambda environment (ignored if ignore_packages is False)
            ignore_packages: Ignore packages that already exist in the AWS lambda environment
        """
        self._reqs = None
        self._pip = None
        self.output_dir = Path(output_dir)
        if ("python" + python_version, architecture) not in PLATFORMS:
            raise Exception(f"{architecture} {python_version} not supported")  # pragma: no cover
        self.project_path = Path(project_path)
        self.python_version = python_version
        self.architecture = architecture
        self.region = region
        self.update_dependencies = update_dependencies
        self.ignore_packages = ignore_packages
        analyzer: Type[DepAnalyzer]
        if (self.project_path / "pyproject.toml").exists() and not (self.project_path / "requirements.txt").exists():
            LOG.info("pyproject.toml found and not requirements.txt, assuming poetry")
            analyzer = PoetryAnalyzer
        elif (self.project_path / "requirements.txt").exists() and not (self.project_path / "pyproject.toml").exists():
            LOG.info("requirements.txt found, assuming pip")
            analyzer = PipAnalyzer
        else:
            raise Exception("Ambiguous project type, quitting")
        self.analyzer = analyzer(
            self.project_path,
            python_version=self.python_version,
            architecture=self.architecture,
            region=self.region,
            ignore_packages=self.ignore_packages,
            update_dependencies=self.update_dependencies,
        )

    def get_total_size(self):
        total_size = 0
        for dirpath, _, filenames in os.walk(self.output_dir):  # noqa: B007
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return total_size

    def get_aws_wrangler_pyarrow(self):
        if "pyarrow" not in self.analyzer.requirements:
            LOG.warning(
                "No pyarrow requirement found in requirements.txt, not bothering to get the aws_wrangler version"
            )
            return
        vers_str = "==" + self.analyzer.requirements["pyarrow"].version
        for p in self.output_dir.glob("pyarrow*"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
        try:
            fetch_package(
                "pyarrow",
                self.output_dir,
                vers_str,
                python_version=self.python_version.lstrip("python"),
                arch=self.architecture,
            )
        except ValueError:
            LOG.warning("pyarrow version %s not found", vers_str)

    def strip_tests(self):
        LOG.warning("Stripping tests")
        for p in self.output_dir.glob("**/*"):
            if p.is_file() and "tests" in p.relative_to(self.output_dir).parts:
                LOG.debug("Stripping test file %s", p)
                p.unlink(missing_ok=True)

    def compile_python(self):
        if self.python_version.lstrip("python") == ".".join(platform.python_version_tuple()[:2]):
            LOG.warning("Compiling package")
            LOG.debug('Target Python version: "%s"', self.python_version)
            LOG.debug('Build Python Version: "%s"', ".".join(platform.python_version_tuple()))
            compile_dir(self.output_dir, quiet=2, optimize=2, workers=0, legacy=True)
            return True
        LOG.warning("Not compiling package, python version mismatch")
        return False

    def strip_python(self):
        LOG.warning("Stripping python scripts")
        for p in self.output_dir.glob("**/*"):
            if p.is_file() and p.name.endswith(".py"):
                LOG.debug("Stripping python file %s", p)
                p.unlink()

    def strip_other_files(self):
        LOG.warning("Stripping other files")
        for p in self.output_dir.glob("**/*"):
            if p.is_file() and p.suffix in (".pyx", ".pyi", ".pxi", ".pxd", ".c", ".h", ".cc"):
                LOG.debug("Stripping file %s", p)
                p.unlink()

    def strip_libraries(self):
        try:
            LOG.warning("Stripping libraries")
            strip_command = get_strip_binary(self.architecture)
            for p in self.output_dir.glob("**/*.so*"):
                LOG.debug('Stripping library "%s"', p)
                subprocess.run([strip_command, str(p)])  # nosec: B603 pylint: disable=subprocess-run-check
        except Exception:  # pylint: disable=broad-except
            LOG.error("Failed to strip libraries, perhaps we don't have the 'strip' command?")

    def zip_output(self, zip_output):
        if isinstance(zip_output, bool):
            zip_path = Path(str(self.output_dir) + ".zip")
        else:
            zip_path = Path(zip_output)
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as zip_file:
            for f in self.output_dir.glob("**/*"):
                if f.is_file():
                    zip_file.write(f, f.relative_to(self.output_dir))

    def package(  # noqa: C901
        self,
        no_clobber: bool = False,
        zip_output: Union[bool, str] = False,
        compile_python: bool = False,
        use_wrangler_pyarrow: bool = False,
        strip_tests: bool = False,
        strip_libraries: bool = False,
        strip_python: bool = False,
        strip_other_files: bool = False,
    ):  # pylint: disable=too-many-arguments,too-many-branches
        if not no_clobber and os.path.exists(self.output_dir):
            LOG.warning("Output directory %s already exists, removing it", self.output_dir)
            shutil.rmtree(self.output_dir, ignore_errors=True)

        self.analyzer.install_dependencies()
        self.analyzer.install_root()
        self.analyzer.copy_from_target(self.output_dir)

        if use_wrangler_pyarrow:
            self.get_aws_wrangler_pyarrow()

        if compile_python:
            compiled = self.compile_python()
            if strip_python:
                if compiled:
                    self.strip_python()
                else:
                    LOG.warning("Unabled to compile python, not stripping python")
        elif strip_python:
            LOG.warning("Not stripping python, since compile_python is set to False")

        if strip_other_files:
            self.strip_other_files()

        if strip_tests:
            self.strip_tests()

        if strip_libraries:
            self.strip_libraries()
        size_out = self.get_total_size()
        if size_out > MAX_LAMBDA_SIZE:
            LOG.error(
                "Package size %s exceeds maximum lambda size %s", sizeof_fmt(size_out), sizeof_fmt(MAX_LAMBDA_SIZE)
            )
        else:
            LOG.warning("Package size: %s", sizeof_fmt(size_out))
        if zip_output:
            LOG.warning("Zipping output")
            self.zip_output(zip_output)


def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def get_strip_binary(architecture="x86_64"):
    if architecture == "x86_64":
        c = shutil.which("x86_64-linux-gnu-strip") or shutil.which("strip")

    elif architecture == "arm64":
        c = shutil.which("aarch64-linux-gnu-strip")
    else:
        raise ValueError(f"Unknown architecture {architecture}")

    if c is None:
        arch = "aarch64" if architecture == "arm64" else "x86_64"
        LOG.error(
            'Could not find "strip" binary for architecture "%s", perhaps install it with "apt-get install binutils-%s-linux-gnu"?',
            architecture,
            arch,
        )
        raise FileNotFoundError(f"Could not find strip binary for {architecture}")
    return c
