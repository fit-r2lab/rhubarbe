#!/usr/bin/env python3

"""
packaging / installing
"""

# c0103: should use uppercase names
# c0326: no space allowed around keyword argument assignment
# pylint: disable=c0103,c0326

import setuptools

from rhubarbe.version import __version__

LONG_DESCRIPTION = \
    "See https://github.com/parmentelat/rhubarbe/blob/main/README.md"

# subcommands
#
# avoid this; we may need this early, when requirements are not met yet
# from rhubarbe.main import supported_subcommands
# instead, cut'n'paste from the rhubarbe help message
rhubarbe_help = (
    "nodes,status,on,off,reset,info,usrpstatus,usrpon,usrpoff,"
    "bye,load,save,wait,images,resolve,share,leases,"
    "monitornodes,monitorphones,monitorleases,accountsmanager,"
    "inventory,config,template,script,version"
)
supported_subcommands = rhubarbe_help.split(",")

# requirements
#
# *NOTE* for ubuntu: also run this beforehand
# apt-get -y install libffi-dev
# which is required before pip can install asyncssh
INSTALL_REQUIRES = [
    'telnetlib3',
    'aiohttp',
    'asyncssh',
    'progressbar33',
    # for monitors
    'asynciojobs',
    'r2lab',
    # for MapDataFrame
    'pandas',
    # not yet used
    'aioxmlrpc',
]

# add convenience entry points like rhubarbe-load
all_commands = (
    ['rhubarbe'] +
    [f'rhubarbe-{subcommand}' for subcommand in supported_subcommands])

setuptools.setup(
    name="rhubarbe",
    author="Thierry Parmentelat",
    author_email="thierry.parmentelat@inria.fr",
    description="Testbed Management Framework for R2Lab",
    long_description=LONG_DESCRIPTION,
    license="CC BY-SA 4.0",
    keywords=['R2lab', 'networking testbed'],

    packages=['rhubarbe', 'rhubarbe.monitor'],
    version=__version__,
    python_requires=">=3.5",

    entry_points={
        'console_scripts': [
            '{} = rhubarbe.__main__:main'
            .format(command) for command in all_commands
        ]
    },
    package_data={
        'rhubarbe': [
            'config/*.conf',
            'config/*.template',
            'scripts/*',
        ],
    },

    install_requires=INSTALL_REQUIRES,

    project_urls={
        'source': "https://github.com/parmentelat/rhubarbe/",
    },

    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Information Technology",
        "Programming Language :: Python :: 3.5",
    ],
)
