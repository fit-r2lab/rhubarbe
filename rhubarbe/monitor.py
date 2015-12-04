import time
import re
import json
import asyncio
# to connect to sidecar
from socketIO_client import SocketIO, LoggingNamespace

from rhubarbe.config import Config

########## use a dedicated logger for monitor
from rhubarbe.logger import monitor_logger as logger
from rhubarbe.node import Node
from rhubarbe.ssh import SshProxy

##########
class ReconnectableSocketIO:
    """
    can emit a message to a socketio service, or reconnect to it
    NOTE that this implementation is not robust, in the sense 
    that when we attempt to emit a message to a disconnected service,
    this message is dropped
    """

    def __init__(self, hostname, port, debug=False):
        self.hostname = hostname
        self.port = port
        self.debug = debug
        self.socketio = None

    def __repr__(self):
        return "socket.io sidecar ws://{}:{}/"\
            .format(self.hostname, self.port)
    
    # at some point we were running into the same issue as this one:
    # https://github.com/liris/websocket-client/issues/222
    # hopefully this harmless glitch has gone away
    def connect(self):
        action = "connect" if self.socketio is None else "reconnect"
        try:
            logger.info("{}ing to {}".format(action, self))
            self.socketio = SocketIO(self.hostname, self.port, LoggingNamespace)
        except:
            logger.warn("Connection lost to {}".format(self))
            self.socketio = None

    def emit_info(self, channel, info):
        if self.debug: logger.info("emitting id={} : {}".format(info['id'], one_char_summary(info)))
        message = json.dumps([info])
        if self.socketio is None:
            self.connect()
        try:
            self.socketio.emit(channel, message,
                               ReconnectableSocketIO.callback)
        except:
            # make sure we reconnect later on
            self.socketio = None
            label = "{}: {}".format(info['id'], one_char_summary(info))
            logger.warn("Dropped message {} - channel {} on {}"
                        .format(label, channel, self))
            
    @staticmethod
    def callback(*args, **kwds):
        logger.info('on socketIO response args={} kwds={}'.format(args, kwds))

# translate info into a single char for logging
def one_char_summary(info):
    if 'cmc_on_off' in info and info['cmc_on_off'] != 'on':
        return '.'
    elif 'control_ping' in info and info['control_ping'] != 'on':
        return 'o'
    elif 'control_ssh' in info and info['control_ssh'] != 'on':
        return '0'
    elif 'os_release' in info and 'fedora' in info['os_release']:
        return 'F'
    elif 'os_release' in info and 'ubuntu' in info['os_release']:
        return 'U'
    else:
        return '^'

class MonitorNode:
    """
    the logic for probing a node as part of monitoring
    formerly in fitsophia/website/monitor.py
    . first probes for the CMC status; if off: done
    . second probe for ssh; if on : done
    . third, checks for ping
    """
    
    def __init__(self, node, report_wlan, reconnectable, channel, debug=False):
        self.node = node
        self.report_wlan = report_wlan
        self.reconnectable = reconnectable
        self.channel = channel
        self.debug = debug
        # current info - will be reported to sidecar
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
        
    def report_info(self):
        """
        Send info to sidecar
        """
        self.reconnectable.emit_info(self.channel, self.info)
        
    def set_info_and_report(self, *overrides):
        """
        Convenience to set info and immediately report
        """
        self.set_info(*overrides)
        self.report_info()

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
            if self.debug:
                logger.info("node={self.node} collected {bytes} for device {device} in {rxtx}"
                            .format(**locals()))
            # do we have something on this measurement ?
            if rxtx_key in self.history:
                previous_bytes, previous_time = self.history[rxtx_key]
                info_key = "{device}_{rxtx}_rate".format(**locals())
                new_rate = 8.*(bytes - previous_bytes) / (now - previous_time)
                wlan_info_dict[info_key] = new_rate
                if self.debug:
                    logger.info("node={} computed {} bps for key {} "
                                "- bytes = {}, pr = {}, now = {}, pr = {}"
                                .format(id, new_rate, info_key,
                                        bytes, previous_bytes, now, previous_time))
            # store this measurement for next run
            self.history[rxtx_key] = (bytes, now)
        # xxx would make sense to clean up history for measurements that
        # we were not able to collect at this cycle
        self.set_info({'os_release' : os_release}, padding_dict, wlan_info_dict)

    @asyncio.coroutine
    def probe(self, ping_timeout, ssh_timeout):
        """
        The logic for getting one node's info and send it to sidecar
        """
        node = self.node
        if self.debug: logger.info("entering pass1, info={}".format(self.info))
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
        if self.debug: logger.info("entering pass2, info={}".format(self.info))
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
        if self.debug: logger.info("trying to ssh-connect")
        try:
            connected = yield from asyncio.wait_for(ssh.connect(), timeout=ssh_timeout)
        except asyncio.TimeoutError as e:
            connected = False
        if self.debug: logger.info("connected={}".format(connected))
        if connected:
            command = ";".join(remote_commands)
            output = yield from ssh.run(command)
            self.parse_ssh_output(output, padding_dict)
            # required as otherwise we leak openfiles
            yield from ssh.close()
            self.report_info()
            return
        if self.debug: logger.info("entering pass3, info={}".format(self.info))
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
            retcod = yield from asyncio.wait_for(subprocess.wait(), timeout=ping_timeout)
            self.set_info_and_report({'control_ping' : 'on'})
            return
        except asyncio.TimeoutError as e:
            self.set_info_and_report({'control_ping' : 'off'})
            return

    @asyncio.coroutine
    def probe_forever(self, cycle, ping_timeout, ssh_timeout):
        """
        runs forever, wait <cycle> seconds between 2 runs of probe()
        """
        while True:
            yield from self.probe(ping_timeout, ssh_timeout)
            yield from asyncio.sleep(cycle)
            

class Monitor:
    def __init__(self, cmc_names, message_bus, cycle, report_wlan, debug=False):
        self.cycle = cycle
        self.report_wlan = report_wlan
        self.debug = debug
        the_config = Config()
        hostname = the_config.value('monitor', 'sidecar_hostname')
        port = int(the_config.value('monitor', 'sidecar_port'))
        reconnectable = ReconnectableSocketIO(hostname, port, debug)
        channel = the_config.value('monitor', 'sidecar_channel')
        nodes = [ Node (cmc_name, message_bus) for cmc_name in cmc_names ]
        # xxx always report wlan for now
        self.monitor_nodes = \
            [ MonitorNode (node, True, reconnectable, channel, debug) for node in nodes]
        self.ping_timeout = float(the_config.value('networking', 'ping_timeout'))
        self.ssh_timeout = float(the_config.value('networking', 'ssh_timeout'))
        self.log_period = float(the_config.value('monitor', 'log_period'))

    @asyncio.coroutine
    def run(self):
        return asyncio.gather(
            *[monitor_node.probe_forever(self.cycle,
                                         ping_timeout = self.ping_timeout,
                                         ssh_timeout = self.ssh_timeout)
              for monitor_node in self.monitor_nodes])

    @asyncio.coroutine
    def log(self):
        while True:
            line = "".join([one_char_summary(mnode.info) for mnode in self.monitor_nodes])
            logger.info(line)
            yield from asyncio.sleep(self.log_period)
            

if __name__ == '__main__':
    import sys
    from node import Node
    rebootnames = sys.argv[1:]
    message_bus = asyncio.Queue()
    the_config = Config()
    cycle = the_config.value('monitor', 'cycle')
    monitor = Monitor(rebootnames, message_bus, cycle=2, report_wlan=True)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(monitor.run(), monitor.report()))
