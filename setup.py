#!/usr/bin/env python3

"""
packaging / installing
"""

# c0103: should use uppercase names
# c0326: no space allowed around keyword argument assignment
# pylint: disable=c0103,c0326

import sys
import os
import os.path
from distutils.core import setup
from rhubarbe.version import __version__ as rhubarbe_version

# check python version
major, minor = sys.version_info[0:2]
if not (major == 3 and minor >= 4):
    print("python 3.4 or higher is required")
    exit(1)

# licence is a python builtin variable!
# with open("COPYING") as f:
#    license = f.read()
with open("README.md") as f:
    long_description = f.read()

# avoid this because it may be used early and the
# requirements are not met yet
# from rhubarbe.main import supported_subcommands
# instead, cut'n'paste from the rhubarbe help message
rhubarbe_help = "nodes,status,on,off,reset,info,usrpstatus,usrpon,usrpoff,"\
                "load,save,wait,monitor,monitorphones,accounts,leases,images,"\
                "resolve,share,inventory,config,version"
supported_subcommands = rhubarbe_help.split(",")
subcommand_symlinks = ["bin/rhubarbe-{}".format(subcommand)
                       for subcommand in supported_subcommands]

if sys.argv[1] in ['sdist', ]:
    for subcommand in supported_subcommands:
        link = "bin/rhubarbe-{}".format(subcommand)
        if not os.path.exists(link):
            os.symlink("rhubarbe", link)

###
# requirements - used by pip install
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
    'asynciojobs>=0.10',
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
    url              = "https://github.com/parmentelat/rhubarbe/",
    platforms        = "Linux",
    packages         = [ 'rhubarbe' ],
    data_files       = [
        ('/etc/rhubarbe', [
            'rhubarbe.conf',
            'inventory-nodes.json.template',
            'inventory-phones.json.template',
        ] )
    ],
    scripts          = [
        'bin/rhubarbe',
    ] + subcommand_symlinks,
    install_requires = required_modules,
)
