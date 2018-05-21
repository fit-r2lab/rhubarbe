#!/usr/bin/env python3

"""
packaging / installing
"""

# c0103: should use uppercase names
# c0326: no space allowed around keyword argument assignment
# pylint: disable=c0103,c0326

import setuptools

from rhubarbe.version import __version__ as rhubarbe_version

LONG_DESCRIPTION = \
    "See https://github.com/parmentelat/rhubarbe/blob/master/README.md"

# subcommands
#
# avoid this; we may need this early, when requirements are not met yet
# from rhubarbe.main import supported_subcommands
# instead, cut'n'paste from the rhubarbe help message
rhubarbe_help = "nodes,status,on,off,reset,info,usrpstatus,usrpon,usrpoff,"\
                "load,save,wait,monitor,monitorphones,accounts,leases,images,"\
                "resolve,share,inventory,config,template,version"
supported_subcommands = rhubarbe_help.split(",")

# requirements
#
# *NOTE* for ubuntu: also run this beforehand
# apt-get -y install libffi-dev
# which is required before pip can install asyncssh
INSTALL_REQUIRES = [
    # version 1.0 breaks our code
    'telnetlib3==0.5.0',
    'aiohttp',
    'asyncssh',
    'progressbar33',
    # for monitor
    'socketIO-client',
    'asynciojobs>=0.10',
    # not yet used
    'aioxmlrpc',
]

# add convenience entry points like rhubarbe-load
all_commands = (
    ['rhubarbe'] +
    ['rhubarbe-{}'.format(subcommand)
     for subcommand in supported_subcommands])

setuptools.setup(
    name="rhubarbe",
    version=rhubarbe_version,
    author="Thierry Parmentelat",
    author_email="thierry.parmentelat@inria.fr",
    description="Testbed Management Framework for R2Lab",
    long_description=LONG_DESCRIPTION,
    license="CC BY-SA 4.0",
    url="https://github.com/parmentelat/rhubarbe/",
    packages=['rhubarbe'],
    install_requires=INSTALL_REQUIRES,
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Information Technology",
        "Programming Language :: Python :: 3.5",
    ],
    keywords=['R2lab', 'networking testbed'],
    entry_points={
        'console_scripts': [
            '{} = rhubarbe.__main__:main'
            .format(command) for command in all_commands
        ]
    },
    package_data={
        'rhubarbe': ['config/*.conf', 'config/*.template'],
    },
)
