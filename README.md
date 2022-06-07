# AWS Lambda Python Packager

[![Checks][checks-shield]][checks-url]
[![Codecov][codecov-shield]][codecov-url]



An alternate way to package Python functions for AWS Lambda. Works cross-platform and cross-architecture if binary packages are available for all packages.

```shell
$ lambda-packager -h
usage: lambda-packager [-h] [--ignore-packages] [--update-pyproject]
                       [--python-version PYTHON_VERSION] [--architecture {x86_64,arm64}]
                       [--region REGION] [--verbose] [--zip-output [ZIP_OUTPUT]]
                       [--compile-python] [--use-aws-pyarrow] [--strip-tests] [--strip-libraries]
                       [--strip-python] [--strip-other] [--optimize-all]
                       pyproject_path output_path

AWS Lambda Python Packager

positional arguments:
  pyproject_path        Path to pyproject.toml
  output_path           Path to output directory

optional arguments:
  -h, --help            show this help message and exit
  --ignore-packages     Ignore packages that are already present in the AWS Lambda Python runtime
                        (default: False)
  --update-pyproject    Update pyproject.toml with the ignored packages (ignored if not --ignore-
                        packages) (default: False)
  --python-version PYTHON_VERSION, -pyv PYTHON_VERSION
                        Python version to target (default: 3.9)
  --architecture {x86_64,arm64}, -a {x86_64,arm64}
                        Architecture to target (default: x86_64)
  --region REGION       AWS region to target (default: us-east-1)
  --verbose, -v         Verbose output (may be specified multiple times) (default: 0)
  --zip-output [ZIP_OUTPUT], -z [ZIP_OUTPUT]
                        Output zip file in addition to directory (default: False)

Optimization Options:
  --compile-python      Compile the python bytecode (default: None)
  --use-aws-pyarrow     Use AWS wrangler pyarrow (may result in smaller file size). Pulls from
                        https://github.com/awslabs/aws-data-wrangler/releases/ until it finds a
                        Lambda layer that includes the proper PyArrow version. (default: False)
  --strip-tests         Strip tests from the package
  --strip-libraries     Strip debugging symbols from libraries
  --strip-python        Strip python scripts from the package (requires --compile-python) (note,
                        may need to set an ENV variable of PYTHONOPTIMIZE=2) (default: False)
  --strip-other         Strip other files from the package ('.pyx', '.pyi', '.pxi', '.pxd', '.c',
                        '.h', '.cc')
  --optimize-all, -O    Turns on all size optimizations (equivalent to --strip-tests --strip-
                        libraries --ignore-packages --update-pyproject --strip-other). May be
                        specified multiple times. Second time will also enable --compile-python
                        --strip-python --use-aws-pyarrow (default: 0)

```



[codecov-shield]: https://img.shields.io/codecov/c/github/mumblepins/aws-lambda-python-packager
[codecov-url]: https://app.codecov.io/gh/mumblepins/aws-lambda-python-packager
