from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path

import click
from wheel.cli.pack import pack as whl_pack

from .. import __version__

LOG = logging.getLogger(__name__)


def many_linux_sub(match_obj):
    return f"{match_obj.group(1)}{match_obj.group(2):0>2}_{match_obj.group(3):0>3}"


def pad_many_linux(s):
    # pad version numbers to make sorting easier
    return re.sub(r"(manylinux_)(\d+)_(\d+)", many_linux_sub, s)


def combine_wheel_files(bundle_path: Path, dist_info_dir: Path):
    all_pure = True
    maximum_minimum_tag = "___"
    for whl in bundle_path.glob("*.dist-info/WHEEL"):
        with whl.open("r", encoding="utf8") as fh:
            tags = []
            is_pure = True
            for ln in fh:
                if ":" not in ln:
                    continue
                k, v = ln.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k == "Tag":
                    tags.append(pad_many_linux(v))
                elif k == "Root-Is-Purelib":
                    if v.lower() == "false":
                        is_pure = False
            if not is_pure:
                all_pure = False
                tags = [t for t in tags if not t.startswith("py")]
                if tags:
                    minimum_tag = min(tags)
                    maximum_minimum_tag = max(maximum_minimum_tag, minimum_tag)
    whl_text = [
        "Wheel-Version: 1.0",
        f"Generator: lambda_packager ({__version__})",
        f"Root-Is-Purelib: {str(all_pure).lower()}",
    ]
    if all_pure:
        whl_text.append("Tag: py3-none-any")
    else:
        whl_text.append(f"Tag: {maximum_minimum_tag}")
    output_file: Path = dist_info_dir / "WHEEL"
    with output_file.open("wt", encoding="utf8") as ofh:
        ofh.write("\n".join(whl_text) + "\n")


@click.command()
@click.argument(
    "bundle_path", type=click.Path(exists=True, file_okay=False, resolve_path=True, path_type=Path)
)
@click.argument(
    "output_path",
    type=click.Path(file_okay=False, resolve_path=True, path_type=Path),
    required=False,
    default=".",
)
@click.option("--output-package-name", default="unified_package")
@click.option("--output-package-version", default="0.0.1")
def unify(
    bundle_path: Path,
    output_path: Path,
    output_package_name="unified_package",
    output_package_version="0.0.1",
):
    """Converts a bundled directory into a single wheel."""

    with tempfile.TemporaryDirectory() as td:
        pkg_td = Path(td) / "bundle"
        shutil.copytree(bundle_path, pkg_td)
        dist_info_dir = pkg_td / f"{output_package_name}-{output_package_version}.dist-info"
        dist_info_dir.mkdir(parents=True, exist_ok=True)
        combine_wheel_files(pkg_td, dist_info_dir)
        with open(dist_info_dir / "METADATA", "w", encoding="utf8") as mfh:
            mfh.write(
                "\n".join(
                    [
                        "Metadata-Version: 2.1",
                        f"Name: {output_package_name}",
                        f"Version: {output_package_version}",
                    ]
                )
            )
        di_dir: Path
        for di_dir in pkg_td.glob("*.dist-info"):
            if not di_dir.is_dir() or di_dir.name.startswith(
                f"{output_package_name}-{output_package_version}"
            ):
                continue
            LOG.debug("Deleting %s", di_dir)
            shutil.rmtree(di_dir)
        output_path.mkdir(exist_ok=True, parents=True)
        whl_pack(pkg_td, output_path, None)
