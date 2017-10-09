#!/usr/bin/env python3

from __future__ import print_function

import sys
import os
import os.path
from distutils.core import setup
from rhubarbe.version import version as rhubarbe_version

# check python version
from sys import version_info
major, minor= version_info[0:2]
if not (major == 3 and minor >= 4):
    print("python 3.4 or higher is required")
    exit(1)

# read licence info
with open("COPYING") as f:
    license = f.read()
with open("README.md") as f:
    long_description = f.read()

# avoid this because it may be used early and the
# requirements are not met yet
#from rhubarbe.main import supported_subcommands
#cut'n'paste from the rhubarbe help message
rhubarbe_help = "nodes,status,on,off,reset,info,usrpstatus,usrpon,usrpoff,load,save,wait,monitor,monitorphones,accounts,leases,images,resolve,share,inventory,config,version"
supported_subcommands = rhubarbe_help.split(",")
subcommand_symlinks = [ "bin/rhubarbe-{}".format(subcommand) for subcommand in supported_subcommands ]
if sys.argv[1] in ('sdist'):
    for subcommand in supported_subcommands:
        link = "bin/rhubarbe-{}".format(subcommand)
        if not os.path.exists(link):
            os.symlink("rhubarbe", link)

### requirements - used by pip install
# *NOTE* for ubuntu: also run this beforehand
# apt-get -y install libffi-dev
# which is required before pip can install asyncssh
required_modules = [
    # version 1.0 breaks our code
    'telnetlib3==0.5.0',
    'aiohttp',
    'asyncssh',
    'progressbar33',
    # for monitor
    'socketIO-client',
    'asynciojobs',
    'aioxmlrpc',
]

setup(
    name             = "rhubarbe",
    version          = rhubarbe_version,
    description      = "Testbed Management Framework for R2Lab",
    long_description = long_description,
    license          = license,
    author           = "Thierry Parmentelat",
    author_email     = "thierry.parmentelat@inria.fr",
    download_url     = "http://github/build.onelab.eu/rhubarbe/rhubarbe-{v}.tar.gz".format(v=rhubarbe_version),
    url              = "https://github.com/parmentelat/rhubarbe/tree/master",
    platforms        = "Linux",
    packages         = [ 'rhubarbe' ],
    data_files       =
      [ ('/etc/rhubarbe', [
          'rhubarbe.conf',
          'inventory-nodes.json.template',
          'inventory-phones.json.template',
      ] ) ],
    scripts          = [
        'bin/rhubarbe',
    ] + subcommand_symlinks,
    install_requires = required_modules,
)

