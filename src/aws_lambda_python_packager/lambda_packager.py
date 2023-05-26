"""
AWS Lambda Packager

This script is used to package the lambda function code into a zip file.
It is an alternative to `sam build` and uses poetry to manage dependencies.

"""
from __future__ import annotations

import gzip
import json
import logging
import os
import platform
import re
import shutil
import subprocess  # nosec B404
from compileall import compile_dir
from datetime import datetime
from pathlib import Path
from py_compile import PycInvalidationMode
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from .arrow_fetcher import fetch_arrow_package
from .dep_analyzer import DepAnalyzer
from .pip_analyzer import PipAnalyzer
from .poetry_analyzer import PoetryAnalyzer
from .util import PLATFORMS, PathType

LOG = logging.getLogger(__name__)
MAX_LAMBDA_SIZE = 250 * 1024 * 1024  # 250MB
OTHER_FILE_EXTENSIONS = (".pyx", ".pyi", ".pxi", ".pxd", ".c", ".h", ".cc")


class UnsupportedVersionException(Exception):
    pass


class ProjectTypeException(Exception):
    pass


class LambdaPackager:
    # pylint: disable=too-many-instance-attributes
    layer_dir: PathType | None

    def __init__(
        self,
        project_path: PathType,
        output_dir: PathType,
        python_version: str = "3.9",
        architecture: str = "x86_64",
        region: str = "us-east-1",
        update_dependencies: bool = False,
        ignore_packages: bool = False,
        split_layer: bool = False,
        additional_packages_to_ignore: dict | None = None,
        ignore_unsupported_python: bool = False,
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
        short_python_version = re.sub(r"^(\d(\.\d+)?)(\.\d+)?$", r"\1", python_version)
        if (
            "python" + short_python_version,
            architecture,
        ) not in PLATFORMS and not ignore_unsupported_python:
            raise UnsupportedVersionException(
                f"{architecture} {python_version} not supported"
            )  # pragma: no cover
        self.project_path = Path(project_path)
        self.python_version = python_version
        self.architecture = architecture
        self.region = region
        self.update_dependencies = update_dependencies
        self.ignore_packages = ignore_packages
        self.split_layer = split_layer
        analyzer_type: type[DepAnalyzer]
        if (self.project_path / "pyproject.toml").exists() and not (
            self.project_path / "requirements.txt"
        ).exists():
            LOG.info("pyproject.toml found and not requirements.txt, assuming poetry")
            analyzer_type = PoetryAnalyzer
        elif (self.project_path / "requirements.txt").exists() and not (
            self.project_path / "pyproject.toml"
        ).exists():
            LOG.info("requirements.txt found, assuming pip")
            analyzer_type = PipAnalyzer
        else:
            raise ProjectTypeException("Ambiguous project type, quitting")

        self.analyzer = analyzer_type(
            self.project_path,
            python_version=self.python_version,
            architecture=self.architecture,
            region=self.region,
            ignore_packages=self.ignore_packages,
            update_dependencies=self.update_dependencies,
            additional_packages_to_ignore=additional_packages_to_ignore,
        )

    @classmethod
    def _get_dir_size(cls, d):
        total_size = 0
        for dirpath, _, filenames in os.walk(d):  # noqa: B007
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return total_size

    def get_total_size(self):
        total = self._get_dir_size(self.output_dir)
        # if self.layer_dir:
        #     total += self._get_dir_size(self.layer_dir)
        return total

    def get_aws_wrangler_pyarrow(self):
        if "pyarrow" not in self.analyzer.exported_requirements():
            LOG.warning(
                "No pyarrow requirement found in requirements.txt, not bothering to get the aws_wrangler version"
            )
            return
        vers_str = self.analyzer.requirements["pyarrow"].version
        files_moved = []
        temp_dir = self.output_dir / "old_pyarrow.bak"
        for p in self.output_dir.glob("pyarrow*"):
            old_p = p.resolve()
            new_p = (temp_dir / p.relative_to(self.output_dir)).resolve()
            shutil.move(old_p, new_p)
            files_moved.append((old_p, new_p))
        try:
            fetch_arrow_package(
                self.output_dir,
                vers_str,
                python_version=self.python_version.lstrip("python"),
                arch=self.architecture,
            )
        except ValueError:
            LOG.warning("pyarrow version %s not found", vers_str)
            for old_p, new_p in files_moved:
                shutil.move(new_p, old_p)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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
            compile_dir(
                str(self.output_dir.absolute()),
                ddir="",
                quiet=2,
                optimize=2,
                workers=1,
                legacy=True,
                force=True,
                invalidation_mode=PycInvalidationMode.UNCHECKED_HASH,
            )
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
            if p.is_file() and p.suffix in OTHER_FILE_EXTENSIONS:
                LOG.debug("Stripping file %s", p)
                p.unlink()

    def compress_boto(self):
        LOG.warning("(Re)Compressing botocore and boto3 data files")
        f: Path
        for f in self.output_dir.glob("**/boto[3c]*/data/**/*.json*"):
            if f.name.endswith(".json.gz"):
                _open = gzip.open
                new_name = f
                delete = False
            else:
                _open = open
                new_name = f.with_suffix(".json.gz")
                delete = True
            try:
                with _open(f, "rt") as fh, gzip.GzipFile(
                    new_name, "wb", compresslevel=9, mtime=0
                ) as zfh:
                    # load and dump to decrease unnecessary whitespace,set mtime to 0 to make builds repeatable
                    json_data = json.load(fh)
                    zfh.write(json.dumps(json_data, separators=(",", ":")).encode("utf8"))
            except json.decoder.JSONDecodeError:
                delete = False
            finally:
                if delete:
                    f.unlink(missing_ok=True)

    def strip_libraries(self):
        # noinspection PyBroadException
        try:
            LOG.warning("Stripping libraries")
            strip_command = get_strip_binary(self.architecture)
            for p in self.output_dir.glob("**/*.so*"):
                LOG.debug('Stripping library "%s"', p)
                subprocess.run(  # nosec: B603 pylint: disable=subprocess-run-check
                    [strip_command, str(p)]
                )
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
        zip_output: bool | str = False,
        compile_python: bool = False,
        use_wrangler_pyarrow: bool = False,
        strip_tests: bool = False,  # pylint: disable=unused-argument
        strip_libraries: bool = False,  # pylint: disable=unused-argument
        strip_python: bool = False,
        strip_other_files: bool = False,  # pylint: disable=unused-argument
        compress_boto: bool = False,  # pylint: disable=unused-argument
    ):  # pylint: disable=too-many-arguments,too-many-branches,too-many-locals
        if not no_clobber and os.path.exists(self.output_dir):
            LOG.warning("Output directory %s already exists, removing it", self.output_dir)
            shutil.rmtree(self.output_dir, ignore_errors=True)

        self.analyzer.install_dependencies()

        layer_paths = self.analyzer.get_layer_files()
        self.analyzer.install_root()
        self.analyzer.copy_from_target(self.output_dir)
        initial_size = self.get_total_size()
        LOG.info("Pre-strip size: %s", sizeof_fmt(initial_size))

        if use_wrangler_pyarrow:
            self.get_aws_wrangler_pyarrow()
            new_size = self.get_total_size()
            LOG.info(
                "Switched PyArrow size: %s (%0.1f%%)",
                sizeof_fmt(new_size),
                new_size / initial_size * 100,
            )

        if strip_python and not compile_python:
            LOG.warning("Not stripping python, since compile_python is set to False")
            strip_python = False

        self.set_utime()
        if compile_python:
            compiled = self.compile_python()
            if strip_python and not compiled:
                strip_python = False
                LOG.warning("Unable to compile python, not stripping python")
            new_size = self.get_total_size()
            LOG.info(
                "Compiled size: %s (%0.1f%%)", sizeof_fmt(new_size), new_size / initial_size * 100
            )
        for strip_func in (
            "strip_python",
            "strip_tests",
            "strip_libraries",
            "strip_other_files",
            "compress_boto",
        ):
            if locals()[strip_func]:
                getattr(self, strip_func)()
                new_size = self.get_total_size()
                LOG.info(
                    "%s done, new size: %s (%0.1f%%)",
                    strip_func,
                    sizeof_fmt(new_size),
                    new_size / initial_size * 100,
                )
        if self.split_layer:
            self._layer_splitter(layer_paths)
        size_out = self.get_total_size()
        if size_out > MAX_LAMBDA_SIZE:
            LOG.error(
                "Package size %s exceeds maximum lambda size %s",
                sizeof_fmt(size_out),
                sizeof_fmt(MAX_LAMBDA_SIZE),
            )
        else:
            LOG.warning(
                "Package size: %s (%0.1f%%)", sizeof_fmt(size_out), size_out / initial_size * 100
            )
        if zip_output:
            LOG.warning("Zipping output")
            self.zip_output(zip_output)
        if self.split_layer:
            return self.output_dir / "main", self.output_dir / "layer"
        return self.output_dir, None

    def _layer_splitter(self, layer_paths: list[Path]):
        with TemporaryDirectory() as layer_td, TemporaryDirectory() as main_td:
            for lp in layer_paths:
                lp = self.output_dir / lp
                if not lp.exists():
                    continue
                shutil.move(str(lp.resolve()), layer_td)
            for p in self.output_dir.iterdir():
                shutil.move(str(p.resolve()), main_td)
            main_dir = self.output_dir / "main"
            layer_dir = self.output_dir / "layer"
            shutil.move(layer_td, layer_dir)
            shutil.move(main_td, main_dir)

    def set_utime(self, set_time: int | None = None):
        if set_time is None:
            set_time = int(datetime(2020, 1, 1, 1, 1).timestamp()) * int(1e9)
        for dirpath, _, filenames in os.walk(self.output_dir):  # noqa: B007
            for f in filenames:
                fp = os.path.join(dirpath, f)
                os.utime(fp, ns=(set_time, set_time))


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
