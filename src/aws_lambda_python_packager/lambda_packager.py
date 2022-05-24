# -*- coding: utf-8 -*-
"""
AWS Lambda Packager

This script is used to package the lambda function code into a zip file.
It is an alternative to `sam build` and uses poetry to manage dependencies.

"""
import io
import logging
import os
import platform
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from compileall import compile_dir
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from os import PathLike
from pathlib import Path
from typing import (
    IO,
    Generator,
    List,
    Optional,
    Union,
)
from zipfile import ZIP_DEFLATED, ZipFile

from cleo.io.inputs.argv_input import ArgvInput
from cleo.io.outputs.buffered_output import BufferedOutput
from cleo.io.outputs.output import Verbosity
from cleo.io.outputs.stream_output import StreamOutput
from poetry.console.application import Application as PoetryApplication
from poetry.core.masonry.api import build_sdist
from poetry.utils.env import EnvManager

from aws_lambda_python_packager.aws_wrangler import fetch_package
from aws_lambda_python_packager.util import (
    BufferedStreamOutput,
    chdir,
    chgenv,
    get_lambda_runtimes,
    get_packages_to_ignore,
)

LOG = logging.getLogger(__name__)
PLATFORMS = get_lambda_runtimes()
MAX_LAMBDA_SIZE = 250 * 1024 * 1024  # 250MB


@dataclass
class PoetryApp:
    app: PoetryApplication
    output: BufferedStreamOutput
    error: StreamOutput
    pip: List[str]


class LambdaPackager:
    # pylint: disable=too-many-instance-attributes
    poetry_out: BufferedStreamOutput

    def __init__(
        self,
        pyproject_path: Union[str, PathLike],
        output_dir: Union[str, PathLike],
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
        self._reqs = None
        self.output_dir = Path(output_dir)
        if re.match(r"^\d+\.\d+$", python_version):
            python_version = f"python{python_version}"
        if (python_version, architecture) not in PLATFORMS:
            raise Exception(f"{architecture} {python_version} not supported")  # pragma: no cover
        self.pyproject_path = Path(pyproject_path)
        self.python_version = python_version
        self.architecture = architecture
        self.region = region
        self.update_pyproject = update_pyproject
        self.ignore_packages = ignore_packages
        if ignore_packages:
            self.packages_to_ignore, self.packages_to_ignore_dict = get_packages_to_ignore(
                region, architecture, python_version
            )
        else:
            self.packages_to_ignore = []
            self.packages_to_ignore_dict = {}

        self._poetry_env_dir = tempfile.mkdtemp()
        self._chngenv = partial(
            chgenv,
            POETRY_VIRTUALENVS_CREATE="true",
            POETRY_VIRTUALENVS_IN_PROJECT="false",
            POETRY_VIRTUALENVS_PATH=self._poetry_env_dir,
        )
        self._chdir = partial(chdir, self.pyproject_path)
        self._poetry_app = None

    def __del__(self):
        shutil.rmtree(self._poetry_env_dir, ignore_errors=True)

    def get_total_size(self):
        total_size = 0
        for dirpath, _, filenames in os.walk(self.output_dir):  # noqa: B007
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        return total_size

    @property
    def _poetry(self):
        if self._poetry_app is None:
            with self._chngenv(), self._chdir():
                output = BufferedStreamOutput(sys.stdout)
                error = StreamOutput(sys.stderr)
                app = PoetryApplication()

                app.auto_exits(False)
                app.catch_exceptions(False)

                _io = app.create_io(ArgvInput(["poetry"]), output, error)
                app._configure_io(_io)  # pylint: disable=protected-access
                # use the poetry env to get pip binary
                env_manager = EnvManager(app.poetry)
                em = env_manager.create_venv(_io, force=True)
                pip = em.get_pip_command(embedded=True)
                self._poetry_app = PoetryApp(app, output, error, pip)
        return self._poetry_app

    @contextmanager
    def _set_quiet(self, quiet=False):
        # if return_output:
        if quiet:
            # return stdout but don't print it
            old_verbose = self._poetry.output.verbosity
            old_output = self._poetry.output
            try:
                self._poetry.output = BufferedOutput()
                self._poetry.error.set_verbosity(Verbosity.QUIET)
                yield
            finally:
                self._poetry.output = old_output
                self._poetry.error.set_verbosity(old_verbose)
        else:
            # to stdout and also return it
            yield

    def _run_poetry(self, *args, return_state=False, quiet=False):
        LOG.debug("Running poetry with args: %s", args)
        with self._set_quiet(quiet):
            ex = self._poetry.app.run(
                input=ArgvInput(argv=["poetry", *args]), output=self._poetry.output, error_output=self._poetry.error
            )
            if return_state:
                return not bool(ex)
            if ex:
                raise ChildProcessError(f"'poetry {' '.join(args)}' failed: exit {ex}")  # pragma: no cover
            return self._poetry.output.fetch()

    def _run_pip(self, *args):
        LOG.debug("Running pip with args: %s", args)

        cmd = self._poetry.pip + list(args)
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:  # nosec: B603
            for line in proc.stdout:
                print(line.decode("utf-8").strip())

    def _export_requirements(self, quiet=False):
        return self._run_poetry("export", "--format=requirements.txt", "--without-hashes", quiet=quiet)

    def _lock(self):
        self._run_poetry("lock", "--no-update")

    def _locked(self):
        return self._run_poetry("lock", "--check", return_state=True, quiet=True)

    def _change_pyproject(self):
        LOG.debug("Getting current requirements from pyproject.toml")
        cur_requires = self._export_requirements(quiet=True)
        pkgs_to_add_to_pyproject = {}
        for line in cur_requires.splitlines(True):
            pkg_name, pkg_version = re.match(r"^([^= ]*?)==(\S*).*$", line).groups()
            if pkg_name in self.packages_to_ignore_dict and pkg_version != self.packages_to_ignore_dict[pkg_name]:
                LOG.warning(
                    "%s is currently %s but should be %s", pkg_name, pkg_version, self.packages_to_ignore_dict[pkg_name]
                )
                pkgs_to_add_to_pyproject[pkg_name] = self.packages_to_ignore_dict[pkg_name]
        if len(pkgs_to_add_to_pyproject) > 0:
            Path(self.pyproject_path, f"pyproject.{datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%SZ')}.toml").write_text(
                Path(self.pyproject_path, "pyproject.toml").read_text(encoding="utf8"),
                encoding="utf8",
            )
            LOG.debug(
                "Updating pyproject.toml with %s", ", ".join(f"{k}=={v}" for k, v in pkgs_to_add_to_pyproject.items())
            )
            self._run_poetry("add", "--lock", *[f"{k}=={v}" for k, v in pkgs_to_add_to_pyproject.items()])

    def reqs(self, refresh=False):
        if self._reqs is None or refresh:
            self._reqs = self.export_reqs()
        return self._reqs

    def export_reqs(
        self,
        output: Optional[Union[io.StringIO, IO[str]]] = None,
    ):
        _output: Union[io.StringIO, IO[str]] = output or io.StringIO()

        if not self._locked():
            LOG.warning("not locked, locking")
            self._lock()
        if self.ignore_packages and self.update_pyproject:
            self._change_pyproject()
        reqs = self._export_requirements(quiet=True)

        for line in reqs.splitlines(True):
            if self.ignore_packages and re.sub(r"^([^= ]*?==\S*).*$", r"\1", line).strip() in self.packages_to_ignore:
                LOG.warning(
                    "Ignoring %s as it should be in the AWS Lambda Environment already", line.strip().split(";")[0]
                )
                continue
            _output.write(line)
        if output is None:
            return _output.getvalue()  # type: ignore
        return None

    @contextmanager
    def build_sdist(self) -> Generator[Path, None, None]:
        sdist_location = tempfile.TemporaryDirectory()
        try:
            with self._chdir(), self._chngenv():
                sdist_name = build_sdist(sdist_location.name)
            yield Path(sdist_location.name) / sdist_name
        finally:
            sdist_location.cleanup()

    def _pip_options(self):
        return self._add_architecture(
            [
                "--no-deps",
                "--disable-pip-version-check",
                "--ignore-installed",
                "--no-compile",
                "--python-version",
                self.python_version.replace("python", ""),
                "--implementation",
                "cp",
            ]
        )

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

    def package_depends(self):

        pip_install_command = [
            "install",
            "--target",
            str(self.output_dir.absolute()),
            *self._pip_options(),
            *self.reqs().splitlines(),
        ]
        LOG.debug("pip install command: %s", pip_install_command)
        self._run_pip(*pip_install_command)

    def package_main(self):
        with self.build_sdist() as sdist_path:
            LOG.info('packaging user package "%s"', sdist_path.name.rsplit(".", 2)[0])
            pip_install_command = [
                "install",
                *self._pip_options(),
                "--target",
                str(self.output_dir.absolute()),
                str(sdist_path.absolute()),
            ]
            LOG.debug("pip install command: %s", " ".join(pip_install_command))
            self._run_pip(*pip_install_command)

    def get_aws_wrangler_pyarrow(self):
        try:
            vers_str = re.sub(
                r"pyarrow([^;]*).*", r"\1", [a for a in self.reqs().splitlines(False) if a.startswith("pyarrow")][0]
            ).strip()
        except IndexError:
            LOG.warning(
                "No pyarrow requirement found in requirements.txt, not bothering to get the aws_wrangler version"
            )
            return
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
        LOG.info("Stripping tests")
        for p in self.output_dir.glob("**/*"):
            if p.is_file() and "tests" in p.relative_to(self.output_dir).parts:
                LOG.debug("Stripping test file %s", p)
                p.unlink()

    def compile_python(self):
        if self.python_version.lstrip("python") == ".".join(platform.python_version_tuple()[:2]):
            LOG.info("Compiling package")
            compile_dir(self.output_dir, quiet=2, optimize=2, workers=0, legacy=True)
            return True
        LOG.warning("Not compiling package, python version mismatch")
        return False

    def strip_python(self):
        LOG.info("Stripping pythons scripts")
        for p in self.output_dir.glob("**/*"):
            if p.is_file() and p.name.endswith(".py"):
                LOG.debug("Stripping python file %s", p)
                p.unlink()

    def strip_other_files(self):
        LOG.info("Stripping other files")
        for p in self.output_dir.glob("**/*"):
            if p.is_file() and p.suffix in (".pyx", ".pyi", ".pxi", ".pxd", ".c", ".h", ".cc"):
                LOG.debug("Stripping file %s", p)
                p.unlink()

    def strip_libraries(self):
        try:
            LOG.info("Stripping libraries")
            strip_command = get_strip_binary(self.architecture)
            for p in self.output_dir.glob("**/*.so*"):
                LOG.debug('Stripping library "%s"', p)
                subprocess.run([strip_command, str(p)])  # nosec: B603 pylint: disable=subprocess-run-check
        except Exception:  # pylint: disable=broad-except
            LOG.warning("Failed to strip libraries, perhaps we don't have the 'strip' command?")

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
        self.package_depends()
        self.package_main()

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
            LOG.warning(
                "Package size %s exceeds maximum lambda size %s", sizeof_fmt(size_out), sizeof_fmt(MAX_LAMBDA_SIZE)
            )
        else:
            LOG.info("Package size: %s", sizeof_fmt(size_out))
        if zip_output:
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
