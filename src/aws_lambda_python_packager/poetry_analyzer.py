# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
from contextlib import contextmanager
from functools import partial
from os import PathLike
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    Union,
)

import toml

from .dep_analyzer import (
    CommandNotFoundError,
    DepAnalyzer,
    PackageInfo,
)
from .poetry_hack import export_requirements
from .util import chdir_cm, chgenv_cm


class PoetryAnalyzer(DepAnalyzer):
    analyzer_name = "poetry"

    def __del__(self):
        super().__del__()
        self._poetry_env.cleanup()

    def __init__(
        self,
        project_root: Union[None, str, PathLike],
        python_version: str = "3.9",
        architecture: str = "x86_64",
        region: str = "us-east-1",
        ignore_packages=False,
        update_dependencies=False,
    ):
        super().__init__(project_root, python_version, architecture, region, ignore_packages, update_dependencies)
        self._poetry = shutil.which("poetry")
        if self._poetry is None:
            raise CommandNotFoundError("poetry not found, please install and add to PATH")
        self.copy_to_temp_dir(("poetry.lock", "pyproject.toml"))

        self._poetry_env = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self._chgenv = partial(
            chgenv_cm,
            # POETRY_VIRTUALENVS_CREATE="false",
            POETRY_VIRTUALENVS_IN_PROJECT="false",
            POETRY_VIRTUALENVS_PATH=self._poetry_env.name,
            VIRTUAL_ENV=None,
        )

    def locked(self):
        return self.run_poetry("lock", "--check", return_state=True, quiet=True)

    def lock(self):
        return self.run_poetry("lock", "--no-update", quiet=True)

    def _get_requirements(self) -> Iterable[PackageInfo]:
        output_file = None
        if not self.locked():
            self.log.info("Locking dependencies")
            self.lock()
        try:
            with self._change_context():
                reqs = export_requirements(Path(self._temp_proj_dir.name))

            self.log.debug(  # pylint: disable=logging-not-lazy
                "\n" + "".join([f"requirements.txt>>  {a}" for a in reqs.splitlines(keepends=True)])
            )

            yield from self.process_requirements(reqs.splitlines(keepends=True))
        finally:
            if output_file:
                os.remove(output_file.name)

    def _update_dependency_file(self, pkgs_to_add: Dict[str, PackageInfo]):
        self.backup_files(["pyproject.toml", "poetry.lock"])
        self.log.debug("Updating pyproject.toml with %s", ", ".join(f"{k}=={v}" for k, v in pkgs_to_add.items()))
        self.run_poetry("add", "--lock", *[f"{k}=={v.version}" for k, v in pkgs_to_add.items()])
        self.copy_from_temp_dir(["poetry.lock", "pyproject.toml"])

    @contextmanager
    def _change_context(self):
        with self._chdir(), self._chgenv():
            yield

    @contextmanager
    def _change_project_root_context(self):
        with chdir_cm(self.project_root), self._chgenv():
            yield

    def run_poetry(self, *args, return_state=False, quiet=False, context=None):
        return self.run_command(self._poetry, *args, return_state=return_state, quiet=quiet, context=context)

    def install_root(self):
        initial_dist = {(a, a.lstat()) for a in (self.project_root / "dist").glob("*.tar.gz")}
        self.log.debug("Trying to build package with poetry")

        packaged = self.run_poetry(
            "build", "--format", "sdist", quiet=True, return_state=True, context=self._change_project_root_context
        )
        if packaged:
            self.log.info("Package built with poetry, installing")
            final_dist = {(a, a.lstat()) for a in (self.project_root / "dist").glob("*.tar.gz")}
            pkg = next(iter(final_dist - initial_dist))[0].absolute()
            pip_command = ["--target", self._target.name, "--no-deps", pkg]
            self.log.warning("Installing poetry package using pip in target")
            self._install_pip(*pip_command)
            self.log.warning("Installing poetry package done")
        else:
            self.log.warning("Package not built with poetry, falling back to .py files")
            super().install_root()

    def direct_dependencies(self) -> Dict[str, str]:

        pyproject = self.project_root / "pyproject.toml"
        with pyproject.open() as f:
            data = toml.load(f)
        return data["tool"]["poetry"]["dependencies"]
