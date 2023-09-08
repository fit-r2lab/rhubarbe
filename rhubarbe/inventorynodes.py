"""
Parse inventory json about nodes
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, r1705

import json

from rhubarbe.singleton import Singleton
from rhubarbe.config import Config


class InventoryNodes(metaclass=Singleton):

    def __init__(self):
        the_config = Config()
        with open(the_config.value('testbed', 'inventory_nodes_path')) as feed:
            self._nodes = json.load(feed)

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
                            for k, v in node.items()]))
        print(20*'-', "INVENTORY END")

    def one_control_interface(self):
        return self._nodes[0]['control']['ip']

    def all_control_hostnames(self):
        return (node['control']['hostname'] for node in self._nodes)
