"""
Utility for loading the config
as a layered cake based on several locations
"""

import os
import socket
import configparser
from pathlib import Path

from rhubarbe.singleton import Singleton
from rhubarbe.logger import logger

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w1202,r1705

LOCATIONS = [
    # all the files found in these locations are considered
    # and are all loaded in this order, so the last ones
    # overwrite the first ones
    # path, mandatory
    ("/etc/rhubarbe/rhubarbe.conf", True),
    ("/etc/rhubarbe/rhubarbe.conf.local", False),
    (str(Path.home()/".rhubarbe.conf"), False),
    ("./rhubarbe.conf", False),
    ("./rhubarbe.conf.local", False),
]


class ConfigException(Exception):
    pass


class Config(metaclass=Singleton):

    def __init__(self):
        self.parser = configparser.ConfigParser()
        self.files = []
        # load all configurations when they exist
        for location, mandatory in LOCATIONS:
            if os.path.exists(location):
                self.files.append(location)
                self.parser.read(location)
                logger.info("Loaded config from {}".format(location))
            elif mandatory:
                raise ConfigException("Missing mandatory config file {}"
                                      .format(location))
        #
        self._hostname = None

    def local_hostname(self):
        if not self._hostname:
            self._hostname = socket.gethostname().split('.')[0]
        return self._hostname

    @staticmethod
    def get_or_raise(dictobj, section, key):
        res = dictobj.get(key, None)
        if res is not None:
            return res
        else:
            raise ConfigException("rhubarbe config: "
                                  "missing entry section={} key={}"
                                  .format(section, key))

    def value(self, section, flag):
        if section not in self.parser:
            raise ConfigException("No such section {} in config"
                                  .format(section))
        config_section = self.parser[section]
        hostname = self.local_hostname()
        key = "{flag}.{hostname}".format(flag=flag, hostname=hostname)
        return config_section.get(key, None) \
            or self.get_or_raise(config_section, section, flag)

    # for now
    # the foreseeable tricky part is, this should be a coroutine..
    def available_frisbee_port(self):
        return self.value('networking', 'port')

    # maybe this one too
    def local_control_ip(self):
        # if specified in the config file, then use that
        if ('networking' in self.parser and
                'local_control_ip' in self.parser['networking']):
            return self.parser['networking']['local_control_ip']
        # but otherwise guess it
        # do not import at toplevel to avoid import loop
        from rhubarbe.inventory import Inventory
        the_inventory = Inventory()
        from rhubarbe.guessip import local_ip_on_same_network_as
        ipaddr, _ = local_ip_on_same_network_as(
            the_inventory.one_control_interface())
        return ipaddr

    def display(self, sections):
        for i, file in enumerate(self.files):
            print("{}-th config file = {}".format(i+1, file))

        def match(section, sections):
            return not sections or section in sections
        for sname, section in sorted(self.parser.items()):
            if match(sname, sections) and section:
                print(10*'=', " section {}".format(sname))
                for fname, value in sorted(section.items()):
                    print("{} = {}".format(fname, value))

    @staticmethod
    def check_file_in_path(binary):
        PATH = os.environ['PATH']                       # pylint: disable=c0103
        paths = ['/'] + [p for p in PATH.split(':') if p]
        for path in paths:
            full = Path(path) / binary
            if full.exists():
                return Path(path) / binary
        return False

    def check_binaries(self):
        # imagezip and frisbee are required on the pxe image only
        names = ('server', 'netcat')
        binaries = [self.value('frisbee', name) for name in names]

        for binary in binaries:
            checked = self.check_file_in_path(binary)
            if not checked:
                message = "Binary {} not found in PATH".format(binary)
                logger.critical(message)
                raise Exception(message)
            else:
                print("Found binary {} as {}"
                      .format(binary, checked))
