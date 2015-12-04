import json

from rhubarbe.singleton import Singleton
from rhubarbe.config import Config

class Inventory(metaclass = Singleton):

    def __init__(self):
        the_config = Config()
        with open(the_config.value('testbed', 'inventory_path')) as feed:
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
            for k, v in host.items():
                if v[key] == value:
                    return host, k
        return None, None

    def attached_hostname_info(self, hostname, interface_key='control', info_key='hostname'):
        """
        locate the entry that has at least one hostname equal to 'hostname'
        and returns the 'hostname' attached to that key
        e.g.
        attached_hostname('reboot01', 'control') => 'fit01'
        """
        host, k = self._locate_entry_from_key('hostname', hostname)
        if host and interface_key in host:
            return host[interface_key][info_key]

    def control_ip_from_any_ip(self, ip):
        host, k = self._locate_entry_from_key('ip', ip)
        if host:
            return host['control']['ip']

    def display(self, verbose=False):
        def cell_repr(k, v, verbose):
            if not verbose:
                return "{}:{}".format(k, v['hostname'])
            else:
                return "{}:{}[{}]".format(k, v['hostname'], v['mac'])
        print(20*'-', "INVENTORY CONTENTS")
        for node in self._nodes:
            print(" ".join([cell_repr(k, v, verbose) for k, v in node.items()]))
        print(20*'-', "INVENTORY END")

    def one_control_interface(self):
        return self._nodes[0]['control']['ip']

    def all_control_hostnames(self):
        return (node['control']['hostname'] for node in self._nodes)

