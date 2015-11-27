#!/usr/bin/env python3
# determines local IP address to use for frisbeed

import subprocess
import re
import ipaddress

from rhubarbe.logger import logger

matcher = re.compile(r"inet (?P<address>([0-9]+\.){3}[0-9]+)/(?P<mask>[0-9]+)")

_local_interfaces = None

def local_interfaces():
    global _local_interfaces
    if _local_interfaces is not None:
        return _local_interfaces
    ip_links = subprocess.Popen(
        ['ip', 'address', 'show'],
        stdout = subprocess.PIPE,
        stderr = subprocess.DEVNULL,
        universal_newlines = True
    )
    _local_interfaces = []
    out, err = ip_links.communicate()
    for line in out.split("\n"):
        line = line.strip()
        m = matcher.match(line)
        if m:
            address, mask = m.group('address'), m.group('mask')
            interface = ipaddress.ip_interface("{}/{}".format(address, mask))
            if not interface.is_loopback:
                _local_interfaces.append(interface)
    return _local_interfaces

def local_ip_on_same_network_as(peer):
    """
    Typically if peer is 192.168.3.1 and we have an interface 192.168.3.200/24
    then this will return a tuple of strings 192.168.3.200, 24

    """
    #peer_address = ipaddress.ip_address(peer)
    for interface in local_interfaces():
        n = interface.network
        l = n.prefixlen
        peer_interface = ipaddress.ip_interface("{}/{}".format(peer,l))
        if peer_interface.network == interface.network:
            return str(interface.ip), str(l)
    
if __name__ == '__main__':
    local_ip, mask = local_ip_on_same_network_as("192.168.3.1")
    print("found {}/{}".format(local_ip, mask))
