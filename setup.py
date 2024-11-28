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
    "See https://github.com/fit-r2lab/rhubarbe/blob/main/README.md"

# subcommands
#
# avoid this; we may need this early, when requirements are not met yet
# from rhubarbe.main import supported_subcommands
# instead, cut'n'paste from the rhubarbe help message
rhubarbe_help = (
    "nodes,status,on,off,reset,info,usrpstatus,usrpon,usrpoff,"
    "bye,load,save,wait,images,resolve,share,leases,"
    "monitornodes,monitorphones,monitorpdus,monitorleases,"
    "accountsmanager,inventory,config,template,pdu,version"
)
supported_subcommands = rhubarbe_help.split(",")

# requirements
#
# *NOTE* for ubuntu: also run this beforehand
# apt-get -y install libffi-dev
# which is required before pip can install asyncssh
INSTALL_REQUIRES = [
    'asyncssh',
    'telnetlib3',
    'aiohttp',
    'progressbar33',
    # for PDUs and yaml deserialization
    'dataclass_wizard',
    # for monitors
    'asynciojobs',
    'r2lab',
    # for MapDataFrame
    'pandas',
    # not yet used
    #'aioxmlrpc',
    # we are NOT using aioping, because it requires root privileges,
    # while forking a ping command works for everybody
    'websockets>=14',
]

# add convenience entry points like rhubarbe-load and others
console_scripts = []
console_scripts.append('rhubarbe = rhubarbe.__main__:main')
for subcommand in supported_subcommands:
    console_scripts.append(f'rhubarbe-{subcommand} = rhubarbe.__main__:main')

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
    python_requires=">=3.10",

    entry_points={ 'console_scripts': console_scripts },
    package_data={
        'rhubarbe': [
            'config/*.conf',
            'config/*.template',
            'scripts/*',
        ],
    },

    install_requires=INSTALL_REQUIRES,

    project_urls={
        'source': "https://github.com/fit-r2lab/rhubarbe/",
    },

    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Information Technology",
        "Programming Language :: Python :: 3.10",
    ],
)
