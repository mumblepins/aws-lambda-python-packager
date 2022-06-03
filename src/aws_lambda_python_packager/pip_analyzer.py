# -*- coding: utf-8 -*-
import tempfile
from importlib.metadata import distributions
from os import PathLike
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    Union,
)

from .dep_analyzer import DepAnalyzer, PackageInfo


def get_packages(path):
    if not isinstance(path, list):
        path = [str(path)]
    dists = distributions(path=path)
    # noinspection PyProtectedMember
    return {b.metadata["Name"]: b.version for b in dists}


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
        # try:
        #     import pkg_resources
        # except ImportError:
        #     self.log.error("pip is not installed")
        #     raise
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

            for pkg, version in get_packages(tmpdir).items():
                yield PackageInfo(pkg, version, f"{pkg}=={version}")

    def _update_dependency_file(self, pkgs_to_add: Dict[str, PackageInfo]):
        self.backup_files(["requirements.txt"])
        self.log.debug("Updating requirements.txt with %s", ", ".join(f"{k}=={v}" for k, v in pkgs_to_add.items()))
        new_lines = []
        with open(Path(self._temp_proj_dir.name) / "requirements.txt", "r", encoding="utf8") as f:
            for pkg in self.process_requirements(f):
                if pkg.name not in pkgs_to_add:
                    new_lines.append(pkg.version_spec)
            for pkg in pkgs_to_add.values():
                new_lines.append(pkg.version_spec)
        with open(Path(self._temp_proj_dir.name) / "requirements.txt", "w", encoding="utf8") as f:
            f.write("\n".join(new_lines))
            f.write("\n")
        self.copy_from_temp_dir(["requirements.txt"])

    def direct_dependencies(self) -> Dict[str, str]:
        with (self.project_root / "requirements.txt").open() as f:
            return {a.name: a.version for a in self.process_requirements(f)}
