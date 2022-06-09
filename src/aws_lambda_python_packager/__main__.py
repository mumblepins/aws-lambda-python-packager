# -*- coding: utf-8 -*-
import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from ._log_formatter import _CustomLogFormatter
from .lambda_packager import LambdaPackager

LOG = logging.getLogger(__name__)


def arg_parser():
    parser = argparse.ArgumentParser(
        description="AWS Lambda Python Packager", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("project_path", help="Path to project directory with pyproject.toml or requirements.txt")
    parser.add_argument("output_path", help="Path to output directory")

    parser.add_argument("--python-version", "-pyv", help="Python version to target", default="3.9")
    parser.add_argument(
        "--architecture", "-a", help="Architecture to target", default="x86_64", choices=["x86_64", "arm64"]
    )
    parser.add_argument("--region", help="AWS region to target", default="us-east-1")
    parser.add_argument(
        "--verbose", "-v", help="Verbose output (may be specified multiple times)", action="count", default=0
    )
    parser.add_argument(
        "--zip-output", "-z", help="Output zip file in addition to directory", const=True, nargs="?", default=False
    )

    parser.add_argument("--version", "-V", action="version", version=f"%(prog)s {__version__}")

    opt_group = parser.add_argument_group("Optimization Options")
    opt_group.add_argument(
        "--ignore-packages",
        help="Ignore packages that are already present in the AWS Lambda Python runtime",
        action="store_true",
    )
    opt_group.add_argument(
        "--update-dependencies",
        help="Update project dependency file with the ignored packages (ignored if not --ignore-packages)",
        action="store_true",
    )
    opt_group.add_argument(
        "--compile-python",
        help="Compile the python bytecode ",
        action="store_const",
        const=True,
    )
    opt_group.add_argument(
        "--use-aws-pyarrow",
        help="Use AWS wrangler pyarrow (may result in smaller file size). "
        "Pulls from https://github.com/awslabs/aws-data-wrangler/releases/ until it finds a "
        "Lambda layer that includes the proper PyArrow version.",
        action="store_true",
    )
    opt_group.add_argument(
        "--strip-tests",
        help="Strip tests from the package",
        action="store_const",
        const=True,
        default=argparse.SUPPRESS,
    )
    opt_group.add_argument(
        "--strip-libraries",
        help="Strip debugging symbols from libraries",
        action="store_const",
        const=True,
        default=argparse.SUPPRESS,
    )
    opt_group.add_argument(
        "--strip-python",
        help="Strip python scripts from the package (requires --compile-python) (note, may need to set an ENV variable of PYTHONOPTIMIZE=2)",
        action="store_true",
    )
    opt_group.add_argument(
        "--strip-other",
        help="Strip other files from the package ('.pyx', '.pyi', '.pxi', '.pxd', '.c', '.h', '.cc')",
        action="store_const",
        const=True,
        default=argparse.SUPPRESS,
    )
    opt_group.add_argument(
        "--optimize-all",
        "-O",
        help="Turns on all size optimizations (equivalent to --strip-tests --strip-libraries --ignore-packages --update-dependencies --strip-other). "
        "May be specified multiple times. Second time will also enable --compile-python --strip-python --use-aws-pyarrow",
        action="count",
        default=0,
    )
    return parser


def main_cli():  # pragma: no cover
    main_args(sys.argv[1:])


def main_args(args):
    parser = arg_parser()
    args = parser.parse_args(args)
    if args.strip_python and not args.compile_python:
        parser.error("--strip-python requires --compile-python")

    args_dict = vars(args).copy()
    if args_dict["optimize_all"] >= 1:
        args_dict["strip_tests"] = True
        args_dict["strip_libraries"] = True
        args_dict["ignore_packages"] = True
        args_dict["update_dependencies"] = True
        args_dict["strip_other"] = True
    if args_dict["optimize_all"] >= 2:
        args_dict["strip_python"] = True
        args_dict["compile_python"] = True
        args_dict["use_aws_pyarrow"] = True
    ch = logging.StreamHandler()
    ch.setFormatter(_CustomLogFormatter())

    if args_dict["verbose"] <= 0:  # pragma: no cover
        log_level = logging.WARNING
    elif args_dict["verbose"] == 1:  # pragma: no cover
        log_level = logging.INFO
    else:
        log_level = logging.DEBUG
    ch.setLevel(log_level)
    logging.getLogger().setLevel(log_level)
    logging.getLogger().addHandler(ch)
    logging.getLogger("fsspec").setLevel(logging.INFO)
    LOG.debug("Processed Args: %s", args)

    if "optimize_all" in args_dict:
        del args_dict["optimize_all"]

    main(**args_dict)


def main(
    project_path,
    output_path,
    ignore_packages=False,
    update_dependencies=False,
    python_version="3.9",
    architecture="x86_64",
    region="us-east-1",
    zip_output=False,
    compile_python=False,
    use_aws_pyarrow=False,
    strip_tests=False,
    strip_libraries=False,
    strip_python=False,
    strip_other=False,
    **_,
):  # pylint: disable=too-many-arguments,too-many-locals
    pp_path = Path(project_path)
    if pp_path.is_file() and pp_path.name in ("pyproject.toml", "requirements.txt"):
        pp_path = pp_path.parent

    lp = LambdaPackager(
        project_path=pp_path,
        output_dir=output_path,
        ignore_packages=ignore_packages,
        update_dependencies=update_dependencies,
        python_version=python_version,
        architecture=architecture,
        region=region,
    )
    lp.package(
        zip_output=zip_output,
        compile_python=compile_python,
        use_wrangler_pyarrow=use_aws_pyarrow,
        strip_tests=strip_tests,
        strip_libraries=strip_libraries,
        strip_python=strip_python,
        strip_other_files=strip_other,
    )


if __name__ == "__main__":  # pragma: no cover
    main_cli()
