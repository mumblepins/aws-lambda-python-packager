# -*- coding: utf-8 -*-
import sys
import tempfile
from os import PathLike
from pathlib import Path
from typing import Iterable, Union

import pkg_resources  # nodep

from .dep_analyzer import DepAnalyzer, PackageInfo


class PipAnalyzer(DepAnalyzer):
    analyzer_name = "pip"

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
        self.copy_to_temp_dir(("requirements.txt",))

    def _get_requirements(self) -> Iterable[PackageInfo]:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._install_pip(
                "--only-binary=:all:",
                "--target",
                tmpdir,
                "-r",
                Path(self._temp_proj_dir.name) / "requirements.txt",
                quiet=True,
            )
            old_path = sys.path.copy()
            try:
                sys.path = [tmpdir]
                # sys.path.insert(0, tmpdir)

                # noinspection PyProtectedMember
                pkg_resources._initialize_master_working_set()  # type: ignore
                pkg_list = [pkg for pkg in pkg_resources.working_set if pkg.location.startswith(tmpdir)]
            finally:
                sys.path = old_path
                # noinspection PyProtectedMember
                pkg_resources._initialize_master_working_set()  # type: ignore
            for pkg in pkg_list:
                yield PackageInfo(pkg.project_name, pkg.version, str(pkg.as_requirement()))

    def _update_dependency_file(self, pkgs_to_add: dict[str, PackageInfo]):
        self.backup_files(["requirements.txt"])
        self.log.debug("Updating requirements.txt with %s", ", ".join(f"{k}=={v}" for k, v in pkgs_to_add.items()))
        new_lines = []
        with open(Path(self._temp_proj_dir.name) / "requirements.txt", "r") as f:
            for pkg in self.process_requirements(f):
                if pkg.name not in pkgs_to_add:
                    new_lines.append(pkg.version_spec)
            for pkg in pkgs_to_add.values():
                new_lines.append(pkg.version_spec)
        with open(Path(self._temp_proj_dir.name) / "requirements.txt", "w") as f:
            f.write("\n".join(new_lines))
            f.write("\n")
        self.copy_from_temp_dir(["requirements.txt"])

    def direct_dependencies(self) -> dict[str, str]:
        with (self.project_root / "requirements.txt").open() as f:
            return {a.name: a.version for a in self.process_requirements(f)}
