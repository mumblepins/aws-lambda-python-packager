from __future__ import annotations

import logging
from pathlib import Path
from pprint import pformat

import click
from click_option_group import optgroup

from ..dep_analyzer import DepAnalyzer, PackageInfo
from ..lambda_packager import OTHER_FILE_EXTENSIONS, LambdaPackager
from ..util import get_glue_libraries

LOG = logging.getLogger(__name__)

OPTIMIZATION_LEVELS = [
    ("strip_tests", "strip_other", "compress_boto"),
    ("ignore_packages", "strip_libraries"),
    ("update_dependencies",),
    ("use_aws_pyarrow",),
    ("strip_python", "compile_python"),
]
_opt_levels = []
for n, ol in enumerate(OPTIMIZATION_LEVELS, 1):
    olt = ", ".join([a.replace("_", "-") for a in ol])
    _opt_levels.append(f"{n}: {olt}")
OPT_LEVEL_TEXT = "\n".join(_opt_levels)


def compile_python_callback(ctx, opt, val):  # pylint: disable=unused-argument
    if "strip_python" not in ctx.params:
        return val
    if ctx.params["strip_python"] and not val:
        raise click.UsageError("--strip-python requires --compile-python")
    return val


def strip_python_callback(ctx, opt, val):  # pylint: disable=unused-argument
    if "compile_python" not in ctx.params:
        return val
    if val and not ctx.params["compile_python"]:
        raise click.UsageError("--strip-python requires --compile-python")
    return val


def optimize_callback(ctx, opt, val):  # pylint: disable=unused-argument
    cmd = ctx.command
    params = {a.name: a for a in cmd.params if not getattr(a, "hidden", False)}
    for oln, opts in enumerate(OPTIMIZATION_LEVELS, 1):
        for o in opts:
            if val < oln:
                params[o].default = False
            else:
                params[o].default = True
    return val


@click.command()
@click.argument("project_path", type=click.Path(exists=True, resolve_path=True, path_type=Path))
@click.argument("output_path", type=click.Path(file_okay=False, resolve_path=True, path_type=Path))
@optgroup.group("Target Options")
@optgroup.option("-pyv", "--python-version", help="Python version to target", default="3.9")
@optgroup.option(
    "-a",
    "--architecture",
    help="Architecture to target",
    type=click.Choice(["x86_64", "arm64"], case_sensitive=False),
    default="x86_64",
)
@optgroup.option("--region", help="AWS region to target", default="us-east-1")
@optgroup.option(
    "--ignore-unsupported-python",
    help="Allow Python versions that are unsupported",
    default=False,
    is_flag=True,
)
@optgroup.group("Output Options")
@optgroup.option(
    "--zip-output",
    "-z",
    help="Output zip file in addition to directory",
    is_flag=False,
    flag_value=True,
    default=False,
    type=click.UNPROCESSED,
)
@optgroup.option(
    "--export-requirements",
    help="export installed packages",
    is_flag=False,
    flag_value="requirements.installed.txt",
    default=False,
    type=click.UNPROCESSED,
)
@optgroup.group("Optimization Options")
@optgroup.option(
    "--ignore-packages/--no-ignore-packages",
    default=False,
    help="Ignore packages that are already present in the AWS Lambda Python runtime",
)
@optgroup.option(
    "--update-dependencies/--no-update-dependencies",
    help="Update project dependency file with the ignored packages (ignored if not --ignore-packages)",
    default=False,
)
@optgroup.option(
    "--compile-python/--no-compile-python",
    help="Compile the python bytecode ",
    default=False,
    callback=compile_python_callback,
)
@optgroup.option(
    "--use-aws-pyarrow/--no-use-aws-pyarrow",
    help="Use AWS wrangler pyarrow (may result in smaller file size). "
    "Pulls from https://github.com/awslabs/aws-data-wrangler/releases/ until it finds a "
    "Lambda layer that includes the proper PyArrow version.",
    default=False,
)
@optgroup.option(
    "--strip-tests/--no-strip-tests",
    help="Strip tests from the package",
    default=False,
)
@optgroup.option(
    "--strip-libraries/--no-strip-libraries",
    help="Strip debugging symbols from libraries",
    default=False,
)
@optgroup.option(
    "--strip-python/--no-strip-python",
    help="Strip python scripts from the package (requires --compile-python) (note, may need to set an "
    "ENV variable of PYTHONOPTIMIZE=2)",
    default=False,
    callback=strip_python_callback,
)
@optgroup.option(
    "--strip-other/--no-strip-other",
    help="Strip other files from the package (" + ", ".join(OTHER_FILE_EXTENSIONS) + ")",
    default=False,
)
@optgroup.option(
    "--compress-boto/--no-compress-boto",
    help="Compress boto3/botocore data files if present",
    default=False,
)
@optgroup.option(
    "--ignore-additional",
    help="ignore additional dependencies using requirements file",
    multiple=True,
    type=click.File(),
)
@optgroup.option(
    "--ignore-from-glue",
    help="ignore dependencies already present in Glue Version",
    type=click.Choice(["2", "3"]),
    callback=lambda c, p, v: int(v) if v else None,
)
@optgroup.option(
    "--optimize-all",
    "-O",
    help="Turns on all size optimizations\n\n" + "\b\n" + "Levels:\n" + OPT_LEVEL_TEXT,
    count=True,
    default=0,
    is_eager=True,
    callback=optimize_callback,
)
def build(
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
    compress_boto=False,
    ignore_additional=None,
    export_requirements=False,
    ignore_unsupported_python: bool = False,
    ignore_from_glue: int | None = None,
    **_,
):  # pylint: disable=too-many-arguments,too-many-locals
    """Bundles a group of python dependencies"""
    LOG.info(pformat(click.get_current_context().params, width=150))
    additional_packages_to_ignore = {}
    for ia in ignore_additional:
        for req in DepAnalyzer.process_requirements(list(ia)):
            if isinstance(req, PackageInfo) and req.name:
                additional_packages_to_ignore[req.name] = req.version
    if project_path.is_file() and project_path.name in ("pyproject.toml", "requirements.txt"):
        project_path = project_path.parent
    if ignore_from_glue:
        glue_libs = get_glue_libraries()[ignore_from_glue]
        additional_packages_to_ignore.update(glue_libs)
    if additional_packages_to_ignore and LOG.getEffectiveLevel() <= logging.DEBUG:
        LOG.debug(pformat(additional_packages_to_ignore))

    lp = LambdaPackager(
        project_path=project_path,
        output_dir=output_path,
        ignore_packages=ignore_packages,
        update_dependencies=update_dependencies,
        python_version=python_version,
        architecture=architecture,
        region=region,
        additional_packages_to_ignore=additional_packages_to_ignore,
        ignore_unsupported_python=ignore_unsupported_python,
    )
    lp.package(
        zip_output=zip_output,
        compile_python=compile_python,
        use_wrangler_pyarrow=use_aws_pyarrow,
        strip_tests=strip_tests,
        strip_libraries=strip_libraries,
        strip_python=strip_python,
        strip_other_files=strip_other,
        compress_boto=compress_boto,
    )
    if export_requirements:
        print(export_requirements)
        with open(export_requirements, "w", encoding="utf8") as f:
            for pkg in lp.analyzer.export_requirements():
                f.write(pkg + "\n")
