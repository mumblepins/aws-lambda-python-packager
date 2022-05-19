# -*- coding: utf-8 -*-
import argparse
from pathlib import Path

from aws_lambda_python_packager import lambda_packager


def main():
    parser = argparse.ArgumentParser(description="AWS Lambda Python Packager", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("pyproject_path", help="Path to pyproject.toml")
    parser.add_argument("output_path", help="Path to output directory")
    parser.add_argument("--ignore-packages", help="Ignore pacakges that are already present in the AWS Lambda Python runtime", action="store_true")
    parser.add_argument("--update-pyproject", help="Update pyproject.toml with the ignored packages (ignored if not --ignore-packages)", action="store_true")
    parser.add_argument("--python-version", help="Python version to target", default="3.9")
    parser.add_argument("--architecture", help="Architecture to target", default="x86_64", choices=["x86_64", "arm64"])
    parser.add_argument("--region", help="AWS region to target", default="us-east-1")

    args = parser.parse_args()
    pp_path = Path(args.pyproject_path)
    if pp_path.is_file() and pp_path.name == "pyproject.toml":
        pp_path = pp_path.parent

    lp = lambda_packager.LambdaPackager(
        pyproject_path=pp_path,
        ignore_packages=args.ignore_packages,
        update_pyproject=args.update_pyproject,
        python_version=args.python_version,
        architecture=args.architecture,
        region=args.region,
    )
    lp.package(output_dir=Path(args.output_path))
