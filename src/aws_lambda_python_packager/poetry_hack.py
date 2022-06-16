# -*- coding: utf-8 -*-
"""
Ugly piece of work that looks for the poetry executable, runs it, generates
an error with traceback, uses that to find the location of the poetry
packages, and then imports poetry packages so that the program itself
doesn't need a poetry requirement
"""
from __future__ import annotations

import re
import shutil
import subprocess  # nosec
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable

path_to_url: Callable[[Any], str]
HTTPRepository: Any


def _get_poetry_env():
    pp = shutil.which("poetry")
    # run a bad command to get a traceback (UGLY HACK)
    r = subprocess.run(  # pylint: disable=subprocess-run-check # nosec
        [pp, "version", "asdfasdf", "-vvv"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    poetry_path = None
    for line in r.stderr.decode().splitlines():
        if grp := re.match(r"^\s*\d+\s*(.*?site-packages)/poetry[^:]*:.*parse\s*$", line):
            poetry_path = grp.groups()[0]
            break
    if poetry_path is None:
        raise Exception("Could not find poetry path")

    return Path(re.sub(r"^~", Path.home().as_posix(), poetry_path)).absolute()


def import_poetry_project(path: Path):
    global path_to_url, HTTPRepository  # pylint: disable=global-statement
    poetry_path = _get_poetry_env()
    old_path = sys.path.copy()
    try:
        sys.path.append(str(poetry_path))
        # noinspection PyUnresolvedReferences
        from poetry.core.packages.utils.utils import (  # pylint: disable=import-outside-toplevel,import-error,no-name-in-module
            path_to_url as _path_to_url,
        )

        # noinspection PyUnresolvedReferences
        from poetry.factory import (  # pylint: disable=import-outside-toplevel,import-error,no-name-in-module
            Factory,
        )

        # noinspection PyUnresolvedReferences
        from poetry.repositories.http import (  # pylint: disable=import-outside-toplevel,import-error,no-name-in-module
            HTTPRepository as _HttpRepository,
        )

        path_to_url = _path_to_url
        HTTPRepository = _HttpRepository
        ptry = Factory().create_poetry(path)
        return ptry
    finally:
        sys.path = old_path


def export_requirements(path: Path, with_urls=True, with_credentials=True):  # noqa: C901
    # pylint: disable=too-many-locals,too-many-branches
    # heavily cribbed from
    # https://github.com/python-poetry/poetry-plugin-export/blob/dca6dbb87c8450b6e7e46a572ef8aa8af0692e50/src/poetry_plugin_export/exporter.py#L88
    global path_to_url  # pylint: disable=global-statement,global-variable-not-assigned
    indexes = set()
    content = ""
    dependency_lines = set()

    ptry = import_poetry_project(path)
    for dependency_package in ptry.locker.get_project_dependency_packages(
        ptry.package.requires, ptry.package.python_marker
    ):
        line = ""

        dependency = dependency_package.dependency
        package = dependency_package.package

        if package.develop:
            line += "-e "

        requirement = dependency.to_pep_508(with_extras=False)
        is_direct_local_reference = dependency.is_file() or dependency.is_directory()
        is_direct_remote_reference = dependency.is_vcs() or dependency.is_url()

        if is_direct_remote_reference:
            line = requirement
        elif is_direct_local_reference:
            if dependency.source_url is None:
                raise Exception("Could not find source url")
            dependency_uri = path_to_url(dependency.source_url)
            line = f"{package.complete_name} @ {dependency_uri}"
        else:
            line = f"{package.complete_name}=={package.version}"

        if not is_direct_remote_reference and ";" in requirement:
            markers = requirement.split(";", 1)[1].strip()
            if markers:
                line += f" ; {markers}"

        if not is_direct_remote_reference and not is_direct_local_reference and package.source_url:
            indexes.add(package.source_url)
        dependency_lines.add(line)
    content += "\n".join(sorted(dependency_lines))
    content += "\n"
    if indexes and with_urls:
        # If we have extra indexes, we add them to the beginning of the output
        indexes_header = ""
        for index in sorted(indexes):
            repositories = [
                r
                for r in ptry.pool.repositories
                if isinstance(r, HTTPRepository) and r.url == index.rstrip("/")  # noqa: F821
            ]
            if not repositories:
                continue
            repository = repositories[0]
            url = get_authenticated_url(repository, repository.url) if with_credentials else repository.url
            if ptry.pool.has_default() and repository is ptry.pool.repositories[0]:
                indexes_header = f"--index-url {url}\n"
                continue

            parsed_url = urllib.parse.urlsplit(url)
            if parsed_url.scheme == "http":
                indexes_header += f"--trusted-host {parsed_url.hostname}\n"
            indexes_header += f"--extra-index-url {url}\n"

        content = indexes_header + "\n" + content
    return content


def get_authenticated_url(repository: HTTPRepository, url):
    auth = repository._authenticator
    parsed = urllib.parse.urlparse(url)
    credential = auth.get_credentials_for_url(url)

    if credential.username is not None and credential.password is not None:
        username = urllib.parse.quote(credential.username, safe="")
        password = urllib.parse.quote(credential.password, safe="")
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{username}:{password}@{parsed.hostname}{port}{parsed.path}"

    return url
