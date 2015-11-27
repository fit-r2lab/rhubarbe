#!/usr/bin/env python3

from __future__ import print_function

from sys import version_info
major, minor= version_info[0:2]
if not (major == 3 and minor >= 4):
    print("python 3.4 or higher is required")
    exit(1)

from distutils.core import setup

from version import version as version_tag

# read licence info
with open("COPYING") as f:
    license = f.read()
with open("README.md") as f:
    long_description = f.read()

data_files = [ ('/etc/rhubarbe', [ 'COPYING', 'README.md' ] ) ]

### requirements - used by pip install
# *NOTE* for ubuntu: also run this beforehand
# apt-get -y install libffi-dev
# which is required before pip can install asyncssh
required_modules = [
    'telnetlib3',
    'aiohttp',
    'asyncssh',
    'progressbar3',
]

setup(
    name             = "rhubarbe",
    version          = version_tag,
    description      = "Testbed Management Framework for R2Lab",
    long_description = long_description,
    license          = license,
    author           = "Thierry Parmentelat",
    author_email     = "thierry.parmentelat@inria.fr",
    download_url     = "http://github/build.onelab.eu/rhubarbe/rhubarbe-{v}.tar.gz".format(v=version_tag),
    url              = "https://github.com/parmentelat/fitsophia/tree/master/rhubarbe",
    platforms        = "Linux",
    data_files       = data_files,
    packages         = [ ],
    package_data     = {},
    install_requires = required_modules,
)

