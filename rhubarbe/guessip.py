#!/usr/bin/env python3

"""
determine local IP address to use for frisbeed
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111

import subprocess
import re
import ipaddress

MATCHER = re.compile(r"inet (?P<address>([0-9]+\.){3}[0-9]+)/(?P<mask>[0-9]+)")

_LOCAL_INTERFACES = None


def local_interfaces():
    global _LOCAL_INTERFACES                            # pylint: disable=w0603
    if _LOCAL_INTERFACES is not None:
        return _LOCAL_INTERFACES
    ip_links = subprocess.Popen(
        ['ip', 'address', 'show'],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True
    )
    _LOCAL_INTERFACES = []
    out, _ = ip_links.communicate()
    for line in out.split("\n"):
        line = line.strip()
        match = MATCHER.match(line)
        if match:
            interface = ipaddress.ip_interface(
                f"{match.group('address')}/{match.group('mask')}")
            if not interface.is_loopback:
                _LOCAL_INTERFACES.append(interface)
    return _LOCAL_INTERFACES


def local_ip_on_same_network_as(peer):
    """
    Typically if peer is 192.168.3.1 and we have an interface 192.168.3.200/24
    then this will return a tuple of strings 192.168.3.200, 24
    """
    for interface in local_interfaces():
        length = interface.network.prefixlen
        peer_interface = ipaddress.ip_interface(f"{peer}/{length}")
        if peer_interface.network == interface.network:
            return str(interface.ip), str(length)
    return None


if __name__ == '__main__':
    LOCAL_IP, MASK = local_ip_on_same_network_as("192.168.3.1")
    print(f"found {LOCAL_IP}/{MASK}")
