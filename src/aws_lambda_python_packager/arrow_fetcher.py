import logging
import shutil
import tarfile
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Generator, Optional, Union

import fsspec
import requests
from appdirs import user_cache_dir

PYARROW_BUILDER_RELEASES = "https://api.github.com/repos/mumblepins/pyarrow-builder/releases/tags/{arrow_version}-py{python_version}"
LOG = logging.getLogger(__name__)


def get_arrow_version(arrow_version: str, python_version: str, arch: str) -> Optional[str]:
    r = requests.get(
        PYARROW_BUILDER_RELEASES.format(python_version=python_version, arrow_version=arrow_version),
        timeout=10,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    rj = r.json()
    if arch.lower().startswith("arm"):
        arch = "aarch64"
    elif arch.lower().startswith("amd"):
        arch = "x86_64"

    for a in rj["assets"]:
        if a["name"].endswith(f"{arrow_version}-py{python_version}-{arch}.tar.gz"):
            return a["browser_download_url"]
    return None


@contextmanager
def open_zip_file(url: object) -> Generator[tarfile.TarFile, None, None]:
    for filesystem_type in (
        {
            "args": ("simplecache",),
            "kwargs": {
                "target_protocol": "http",
                "cache_storage": str(
                    (Path(user_cache_dir("lambda-packager")) / "simplecache").resolve()
                ),
            },
        },
        {
            "args": ("http",),
            "kwargs": {},
        },
    ):
        f = z = None
        try:
            fs = fsspec.filesystem(*filesystem_type["args"], **filesystem_type["kwargs"])
            f = fs.open(url, "rb")
            z = tarfile.open(fileobj=f)
            yield z
        except (KeyError, AttributeError):
            continue
        else:
            break
        finally:
            with suppress(NameError, KeyError, AttributeError):
                if z is not None:
                    z.close()
                if f is not None:
                    f.close()


def fetch_arrow_package(
    output_dir: Union[str, Path],
    package_version: str,
    python_version="3.9",
    arch="x86_64",
):
    if (pkg_url := get_arrow_version(package_version, python_version, arch)) is None:
        raise ValueError(f"Could not find package  arrow with version {package_version}")
    with open_zip_file(pkg_url) as zfh, tempfile.TemporaryDirectory() as tmpdir:
        zfh.extractall(tmpdir)

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        for p in (Path(tmpdir) / "python").glob("*"):
            shutil.copytree(p, Path(output_dir) / p.name, dirs_exist_ok=True)
        return package_version
