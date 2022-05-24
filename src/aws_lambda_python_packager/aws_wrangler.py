# -*- coding: utf-8 -*-
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Union
from zipfile import ZipFile

import fsspec
import requests
from appdirs import user_cache_dir
from packaging.specifiers import SpecifierSet
from packaging.version import LegacyVersion

AWS_WRANGLER_RELEASES = "https://api.github.com/repos/awslabs/aws-data-wrangler/releases"
AWS_LAYER_VERSION_URL = "https://github.com/awslabs/aws-data-wrangler/releases/download/{version}/awswrangler-layer-{version}-py{python_version}{arch}.zip"
LOG = logging.getLogger(__name__)
fs = fsspec.filesystem("blockcache", target_protocol="http", cache_storage=user_cache_dir("lambda-packager"))
open = fs.open


def get_all_versions(python_version="3.9", arch="x86_64"):
    r = requests.get(AWS_WRANGLER_RELEASES)
    rj = r.json()
    tags = {v["tag_name"]: v for v in sorted(rj, key=lambda x: LegacyVersion(x["tag_name"]), reverse=True)}

    if arch == "x86_64":
        arch = ""
    elif arch == "arm64":
        arch = "-arm64"
    else:
        raise ValueError("arch must be either 'x86_64' or 'arm64'")
    for v in tags.values():
        for a in v["assets"]:
            if re.match(rf"awswrangler-layer-[\d.]*-py{python_version}{arch}.zip", a["name"]):
                with open(a["browser_download_url"], "rb") as f:
                    with ZipFile(f) as z:
                        z.filename = a["name"]
                        yield z


def fetch_package(
    package_name,
    output_dir: Union[str, Path],
    package_version: Optional[str] = None,
    python_version="3.9",
    arch="x86_64",
):
    if package_version is not None:
        package_version = SpecifierSet(package_version)
    for zfh in get_all_versions(python_version, arch):
        file_list = [a for a in zfh.namelist() if a.startswith(f"python/{package_name}")]
        wr_pkg_version = [
            re.sub(rf"^.*{package_name}-(.*?)\.dist-info.*$", r"\1", a) for a in file_list if "dist-info" in a
        ][0]
        if package_version is None or package_version.contains(wr_pkg_version):
            LOG.info(f"Found package {package_name}-{wr_pkg_version} in {zfh.filename}")
            with tempfile.TemporaryDirectory() as tmpdir:
                zfh.extractall(tmpdir, file_list)
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                for p in (Path(tmpdir) / "python").glob("*"):
                    shutil.copytree(p, Path(output_dir) / p.name, dirs_exist_ok=True)
                return wr_pkg_version
    else:
        raise ValueError(f"Could not find package {package_name} with version {package_version}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("package_name", help="Name of the package to fetch")
    parser.add_argument("output_dir", help="Directory to output the package to")
    parser.add_argument("--package-version", help="Version of aws-data-wrangler to use")
    parser.add_argument("--python-version", help="Python version to use", default="3.9")
    parser.add_argument("--arch", help="Architecture to use", default="x86_64")
    args = parser.parse_args()
    fetch_package(args.package_name, Path(args.output_dir), args.package_version, args.python_version, args.arch)
