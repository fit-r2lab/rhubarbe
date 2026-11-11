"""
Parse inventory yaml about nodes
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, r1705

from pathlib import Path

import yaml

from rhubarbe.singleton import Singleton
from rhubarbe.config import Config


class InventoryNodes(metaclass=Singleton):

    def __init__(self):
        the_config = Config()
        self._path = Path(the_config.value('testbed', 'inventory_nodes_path'))
        self._mtime = None
        self._nodes = []
        self._load()

    def _load(self):
        mtime = self._path.stat().st_mtime
        with self._path.open() as feed:
            self._nodes = yaml.safe_load(feed)['nodes']
        self._mtime = mtime

    def reload_if_changed(self):
        """
        re-read the file if it changed since we read it,
        e.g. because the inventory was published again
        returns True if it was reloaded; on any error, keeps the previous
        contents and returns False
        """
        try:
            if self._path.stat().st_mtime == self._mtime:
                return False
            self._load()
            return True
        except Exception:                       # pylint: disable=w0703
            return False

    # an entry has one sub-record per interface (cmc, control, data),
    # plus possibly this one, that is free-form and exposed as-is
    STATIC_KEY = 'static'

    def static_info(self, cmc_hostname):
        """
        the free-form 'static' record of the node with that cmc hostname,
        e.g. 'reboot12', or None if there is none; we make no assumption
        on its contents
        """
        for host in self._nodes:
            if host['cmc']['hostname'] == cmc_hostname:
                return host.get(self.STATIC_KEY)
        return None

    def _locate_entry_from_key(self, key, value):
        """
        search for an entry that has given hostname
        returns a tuple (host, key)
        e.g.
        _locate_entry_from_key('hostname', 'reboot01') =>
         ( { 'cmc' : {...}, 'control' : {...}, 'data' : {...} }, 'cmc' )
         """
        for host in self._nodes:
            for k, v in host.items():                   # pylint: disable=c0103
                if k == self.STATIC_KEY:
                    continue
                if v[key] == value:
                    return host, k
        return None, None

    def attached_hostname_info(self, hostname,
                               interface_key='control', info_key='hostname'):
        """
        locate the entry that has at least one hostname equal to 'hostname'
        and returns the 'hostname' attached to that key
        e.g.
        attached_hostname('reboot01', 'control') => 'fit01'
        """
        host, _ = self._locate_entry_from_key('hostname', hostname)
        if host and interface_key in host:
            return host[interface_key][info_key]
        return None

    def control_hostname_from_mac(self, mac):
        """
        the control hostname of the node whose control interface
        has that mac address, or None
        """
        for host in self._nodes:
            control = host.get('control')
            if control and control.get('mac', '').lower() == mac.lower():
                return control['hostname']
        return None

    def control_ip_from_any_ip(self, ipaddr):
        host, _ = self._locate_entry_from_key('ip', ipaddr)
        if host:
            return host['control']['ip']
        return None

    def display(self, verbose=False):
        def cell_repr(key, value, verbose):
            if not verbose:
                return f"{key}:{value['hostname']}"
            else:
                return f"{key}:{value['hostname']}[{value['mac']}]"
        print(20*'-', "INVENTORY CONTENTS")
        for node in self._nodes:
            print(" ".join([cell_repr(k, v, verbose)
                            for k, v in node.items()
                            if k != self.STATIC_KEY]))
        print(20*'-', "INVENTORY END")

    def one_control_interface(self):
        return self._nodes[0]['control']['ip']

    def all_control_hostnames(self):
        return (node['control']['hostname'] for node in self._nodes)
