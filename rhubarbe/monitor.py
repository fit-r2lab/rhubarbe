import time
import re
import json
import asyncio
# to connect to sidecar
from socketIO_client import SocketIO, LoggingNamespace

# TODO
# (*)  find proper index!!!
#      could we just remove all alphnum from the cmc_name
# (*) log in /var/log
# (*) and add some one-liner for debugging
# re-check; with node (1) it seemed to work a little weird but in fact the
# regular monitor was still working in the background, so...

from rhubarbe.node import Node
from rhubarbe.ssh import SshProxy
from rhubarbe.config import the_config
from rhubarbe.logger import logger

debug = False
#debug = True

class MonitorNode:
    """
    the logic for probing a node as part of monitoring
    formerly in fitsophia/website/monitor.py
    . first probes for the CMC status; if off: done
    . second probe for ssh; if on : done
    . third, checks for ping
    """
    
    def __init__(self, node, report_wlan, sidecar_socketio, channel):
        self.node = node
        self.report_wlan = report_wlan
        self.sidecar_socketio = sidecar_socketio
        self.channel = channel
        # current info - will be reported to sidecar
        # xxx
        self.info = {'id': node.id }
        # remember previous wlan measurement to compute rate
        self.history = {}

    def set_info(self, *overrides):
        """
        update self.info with all the dicts in overrides
        """
        for override in overrides:
            self.info.update(override)

    def zero_wlan_infos(self):
        """
        set wlan-related attributes to 0.
        """
        for k in self.info:
            if k.startswith('wlan'):
                self.info[k] = 0.
        
    @staticmethod
    def socketio_callback(*args, **kwds):
        logger.info('on socketIO response args={} kwds={}'.format(args, kwds))

    def report_info(self):
        """
        Send info to sidecar
        """
        logger.info("Emitting {}".format(self.info))
        try:
            self.sidecar_socketio.emit(self.channel, json.dumps([self.info]),
                                       MonitorNode.socketio_callback)
        except Exception as e:
            self.logger("need to reconnect to sidecar")
        
    def set_info_and_report(self, *overrides):
        """
        Convenience to set info and immediately report
        """
        self.set_info(*overrides)
        self.report_info()

    def feedback(self, message):
        print("tmp feedback : {}".format(message))


    ubuntu_matcher = re.compile("DISTRIB_RELEASE=(?P<ubuntu_version>[0-9.]+)")
    fedora_matcher = re.compile("Fedora release (?P<fedora_version>\d+)")
    gnuradio_matcher = re.compile("\AGNURADIO:(?P<gnuradio_version>[0-9\.]+)\Z")
    rxtx_matcher = re.compile("==> /sys/class/net/(?P<device>wlan[0-9])/statistics/(?P<rxtx>[rt]x)_bytes <==")
    number_matcher = re.compile("\A[0-9]+\Z")

    def parse_ssh_output(self, stdout, padding_dict):
        flavour = "other"
        extension = ""
        rxtx_dict = {}
        rxtx_key = None
        for line in stdout.split("\n"):
            match = self.ubuntu_matcher.match(line)
            if match:
                version = match.group('ubuntu_version')
                flavour = "ubuntu-{version}".format(**locals())
                continue
            match = self.fedora_matcher.match(line)
            if match:
                version = match.group('fedora_version')
                flavour = "fedora-{version}".format(**locals())
                continue
            match = self.gnuradio_matcher.match(line)
            if match:
                version = match.group('gnuradio_version')
                extension += "-gnuradio-{version}".format(**locals())
                continue
            match = self.rxtx_matcher.match(line)
            if match:
                # use a tuple as the hash
                rxtx_key = (match.group('device'), match.group('rxtx'))
                continue
            match = self.number_matcher.match(line)
            if match and rxtx_key:
                rxtx_dict[rxtx_key] = int(line)
                continue
            rxtx_key = None
            
        os_release = flavour + extension
        # now that we have the counters we need to translate this into rate
        # for that purpose we use local clock; small imprecision should not impact overall result
        now = time.time()
        wlan_info_dict = {}
        for rxtx_key, bytes in rxtx_dict.items():
            device, rxtx = rxtx_key
            self.feedback("node={self.node} collected {bytes} for device {device} in {rxtx}".format(**locals()))
            # do we have something on this measurement ?
            if rxtx_key in self.history:
                previous_bytes, previous_time = self.history[rxtx_key]
                info_key = "{device}_{rxtx}_rate".format(**locals())
                new_rate = 8.*(bytes - previous_bytes) / (now - previous_time)
                wlan_info_dict[info_key] = new_rate
                self.feedback("node={} computed {} bps for key {} "
                              "- bytes = {}, pr = {}, now = {}, pr = {}"
                              .format(id, new_rate, info_key,
                                      bytes, previous_bytes, now, previous_time));
            # store this measurement for next run
            self.history[rxtx_key] = (bytes, now)
        # xxx would make sense to clean up history for measurements that
        # we were not able to collect at this cycle
        self.set_info({'os_release' : os_release}, padding_dict, wlan_info_dict)

    @asyncio.coroutine
    def probe(self):
        """
        The logic for getting one node's info and send it to sidecar
        """
        node = self.node
        if debug: logger.info("entering pass1, info={}".format(self.info))
        # pass1 : check for status
        padding_dict = {
            'control_ping' : 'off',
            'control_ssh' : 'off',
            # don't overwrite os_release though
        }
        status = yield from self.node.get_status()
        if status is None:
            self.set_info_and_report({'cmc_on_off' : 'fail'}, padding_dict)
            return
        if status == "off":
            self.set_info_and_report({'cmc_on_off' : 'off'}, padding_dict)
            return
        if debug: logger.info("entering pass2, info={}".format(self.info))
        # pass2 : node is ON - let's try to ssh it
        self.set_info({'cmc_on_off' : 'on'})
        padding_dict = {
            'control_ping' : 'on',
            'control_ssh' : 'on',
        }
        self.zero_wlan_infos()
        remote_commands = [
            "cat /etc/lsb-release /etc/fedora-release /etc/gnuradio-release 2> /dev/null | grep -i release",
            "echo -n GNURADIO: ; gnuradio-config-info --version 2> /dev/null || echo none",
            ]
        if self.report_wlan:
            remote_commands.append(
                "head /sys/class/net/wlan?/statistics/[rt]x_bytes"                
            )
        ssh = SshProxy(self.node)
        if debug: logger.info("trying to ssh-connect")
        try:
            timeout = float(the_config.value('monitor', 'ssh_timeout'))
            connected = yield from asyncio.wait_for(ssh.connect(), timeout=timeout)
        except asyncio.TimeoutError as e:
            connected = False
        if debug: logger.info("connected={}".format(connected))
        if connected:
            command = ";".join(remote_commands)
            output = yield from ssh.run(command)
            self.parse_ssh_output(output, padding_dict)
            self.report_info()
            return
        if debug: logger.info("entering pass3, info={}".format(self.info))
        # pass3 : node is ON but could not ssh
        # check for ping
        # I don't know of an asyncio library to deal with icmp
        # so let's use asyncio.subprocess
        # xxx maybe a Ping class would be the way to go
        control = self.node.control_hostname()
        command = [ "ping", "-c", "1", "-t", "1", control ]
        try:
            subprocess = yield from asyncio.create_subprocess_exec(
                *command,
                stdout = asyncio.subprocess.DEVNULL,
                stderr = asyncio.subprocess.DEVNULL)
            timeout = float(the_config.value('monitor', 'ping_timeout'))
            retcod = yield from asyncio.wait_for(subprocess.wait(), timeout=timeout)
            self.set_info_and_report({'control_ping' : 'on'})
            return
        except asyncio.TimeoutError as e:
            self.set_info_and_report({'control_ping' : 'off'})
            return

    @asyncio.coroutine
    def probe_forever(self, cycle):
        """
        runs forever, wait <cycle> seconds between 2 runs of probe()
        """
        while True:
            yield from self.probe()
            yield from asyncio.sleep(cycle)
            

class Monitor:
    def __init__(self, cmc_names, message_bus, cycle, report_wlan):
        self.cycle = cycle
        self.report_wlan = report_wlan
        hostname = the_config.value('monitor', 'sidecar_hostname')
        port = int(the_config.value('monitor', 'sidecar_port'))
        # xxx is there a need to reconnect sometimes ?
        logger.info("socketio connecting to hostname={}".format(hostname))
        socketio = SocketIO(hostname, port, LoggingNamespace)
        channel = the_config.value('monitor', 'sidecar_channel')
        nodes = [ Node (cmc_name, message_bus) for cmc_name in cmc_names ]
        # xxx always report wlan for now
        self.monitor_nodes = \
            [ MonitorNode (node, True, socketio, channel) for node in nodes]

    @asyncio.coroutine
    def run(self):
        return asyncio.gather(*[monitor_node.probe_forever(self.cycle)
                                for monitor_node in self.monitor_nodes])

if __name__ == '__main__':
    import sys
    from node import Node
    rebootnames = sys.argv[1:]
    message_bus = asyncio.Queue()
    cycle = the_config.value('monitor', 'cycle')
    monitor = Monitor(rebootnames, message_bus, cycle=2, report_wlan=True)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(monitor.run())
