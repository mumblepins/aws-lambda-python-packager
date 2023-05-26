from __future__ import annotations

import tempfile
from importlib.metadata import distributions
from pathlib import Path
from typing import Iterable

from .dep_analyzer import DepAnalyzer, ExtraLine, PackageInfo
from .util import PathType


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
        project_root: PathType | None,
        python_version: str = "3.9",
        architecture: str = "x86_64",
        region: str = "us-east-1",
        ignore_packages=False,
        update_dependencies=False,
        additional_packages_to_ignore: dict | None = None,
    ):
        super().__init__(
            project_root,
            python_version,
            architecture,
            region,
            ignore_packages,
            update_dependencies,
            additional_packages_to_ignore,
        )
        # try:
        #     import pkg_resources
        # except ImportError:
        #     self.log.error("pip is not installed")
        #     raise
        self.copy_to_temp_dir(("requirements.txt",))

    def _get_requirements(self) -> Iterable[PackageInfo | ExtraLine]:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._install_pip(
                "--only-binary=:all:",
                "--target",
                tmpdir,
                "-r",
                Path(self._temp_proj_dir.name) / "requirements.txt",
                quiet=True,
                requirements_file=True,
            )

            for pkg, version in get_packages(tmpdir).items():
                yield PackageInfo(pkg, version, f"{pkg}=={version}")

    @property
    def extra_lines(self):
        self._extra_lines = []
        req_file = self.project_root / "requirements.txt"
        with req_file.open() as f:
            for line in self.process_requirements(f):
                if not isinstance(line, ExtraLine):
                    continue
                self._extra_lines.append(line)
        return self._extra_lines

    def _update_dependency_file(self, pkgs_to_add: dict[str, PackageInfo]):
        self.backup_files(["requirements.txt"])
        self.log.debug(
            "Updating requirements.txt with %s",
            ", ".join(f"{k}=={v}" for k, v in pkgs_to_add.items()),
        )
        new_lines = []
        with open(Path(self._temp_proj_dir.name) / "requirements.txt", encoding="utf8") as f:
            for pkg in self.process_requirements(f):
                if isinstance(pkg, ExtraLine):
                    new_lines.append(" ".join(pkg))
                    continue
                if pkg.name not in pkgs_to_add:
                    new_lines.append(pkg.version_spec)
            for pkg in pkgs_to_add.values():
                new_lines.append(pkg.version_spec)
        with open(Path(self._temp_proj_dir.name) / "requirements.txt", "w", encoding="utf8") as f:
            f.write("\n".join(new_lines))
            f.write("\n")
        self.copy_from_temp_dir(["requirements.txt"])

    def direct_dependencies(self) -> dict[str, str]:
        with (self.project_root / "requirements.txt").open() as f:
            ret = {}
            for pkg in self.process_requirements(f):
                if isinstance(pkg, ExtraLine):
                    continue
                ret[pkg.name] = pkg.version
            return ret
