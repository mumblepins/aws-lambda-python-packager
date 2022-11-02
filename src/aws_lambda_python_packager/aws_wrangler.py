# -*- coding: utf-8 -*-
import logging
import re
import shutil
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Optional, Union
from zipfile import ZipFile

import fsspec
import requests
from appdirs import user_cache_dir
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

AWS_WRANGLER_RELEASES = "https://api.github.com/repos/awslabs/aws-data-wrangler/releases"
AWS_LAYER_VERSION_URL = "https://github.com/awslabs/aws-data-wrangler/releases/download/{version}/awswrangler-layer-{version}-py{python_version}{arch}.zip"
LOG = logging.getLogger(__name__)
CACHE_METHOD = "blockcache"


def to_version(version):
    try:
        return Version(version)
    except InvalidVersion:
        return None


def get_all_versions(python_version="3.9", arch="x86_64"):
    r = requests.get(AWS_WRANGLER_RELEASES, timeout=10)
    rj = r.json()
    rj = filter(lambda x: to_version(x["tag_name"]) is not None, rj)

    tags = {v["tag_name"]: v for v in sorted(rj, key=lambda x: Version(x["tag_name"]), reverse=True)}

    if arch == "x86_64":
        arch = ""
    elif arch == "arm64":
        arch = "-arm64"
    else:
        raise ValueError("arch must be either 'x86_64' or 'arm64'")
    for v in tags.values():
        for a in v["assets"]:
            if re.match(rf"awswrangler-layer-[\d.]*-py{python_version}{arch}.zip", a["name"]):
                download_url = a["browser_download_url"]
                filename = a["name"]
                yield download_url, filename


@contextmanager
def open_zip_file(url, file):
    for cache_type in ("blockcache", "simplecache"):
        f = z = None
        try:
            fs = fsspec.filesystem(
                cache_type,
                target_protocol="http",
                cache_storage=str((Path(user_cache_dir("lambda-packager")) / cache_type).resolve()),
            )
            f = fs.open(url, "rb")
            z = ZipFile(f)
            z.filename = file
            yield z
        except KeyError:
            continue
        else:
            break
        finally:
            with suppress(NameError, KeyError, AttributeError):
                z.close()
                f.close()


def fetch_package(
    package_name,
    output_dir: Union[str, Path],
    package_version: Optional[str] = None,
    python_version="3.9",
    arch="x86_64",
):
    if package_version is not None:
        pkg_spec = SpecifierSet(package_version)
    else:
        pkg_spec = None
    for url, filename in get_all_versions(python_version, arch):
        with open_zip_file(url, filename) as zfh:
            file_list = [a for a in zfh.namelist() if a.startswith(f"python/{package_name}")]
            wr_pkg_version = [
                re.sub(rf"^.*{package_name}-(.*?)\.dist-info.*$", r"\1", a) for a in file_list if "dist-info" in a
            ][0]
            if pkg_spec is None or pkg_spec.contains(wr_pkg_version):
                LOG.info("Found package %s-%s in %s", package_name, wr_pkg_version, zfh.filename)
                with tempfile.TemporaryDirectory() as tmpdir:
                    zfh.extractall(tmpdir, file_list)
                    Path(output_dir).mkdir(parents=True, exist_ok=True)
                    for p in (Path(tmpdir) / "python").glob("*"):
                        shutil.copytree(p, Path(output_dir) / p.name, dirs_exist_ok=True)
                    return wr_pkg_version
    raise ValueError(f"Could not find package {package_name} with version {package_version}")


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("package_name", help="Name of the package to fetch")
    parser.add_argument("output_dir", help="Directory to output the package to")
    parser.add_argument("--package-version", help="Version of aws-data-wrangler to use")
    parser.add_argument("--python-version", help="Python version to use", default="3.9")
    parser.add_argument("--arch", help="Architecture to use", default="x86_64")
    args = parser.parse_args()
    fetch_package(
        args.package_name,
        Path(args.output_dir),
        args.package_version,
        args.python_version,
        args.arch,
    )
