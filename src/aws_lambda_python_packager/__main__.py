# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

import click
import click_log

from . import __version__
from .cli.build import build
from .cli.unify import unify

LOG = logging.getLogger()

click_log.basic_config()


@click.group(
    context_settings={
        "help_option_names": ["-h", "--help"],
        "show_default": True,
        "max_content_width": 120,
    }
)
@click_log.simple_verbosity_option(logging.getLogger(), default="WARNING")
@click.version_option(__version__, "-V", "--version")
def main():
    pass


main.add_command(build)
main.add_command(unify)

if __name__ == "__main__":  # pragma: no cover
    main()
