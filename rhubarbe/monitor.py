import time
import re
import json
import asyncio
# to connect to sidecar
# at first I would have preferred an asyncio-friendly library
# for talking socket.io; however I could not find one, and
# websockets dod not offer something like 'emit' out of the box
# so, we're still using this synchroneous one
# using .on to arm callbacks, and occasionally calling wait
# to give a chance for the callback to trigger
from socketIO_client import SocketIO, LoggingNamespace

from rhubarbe.config import Config

########## use a dedicated logger for monitor
from rhubarbe.logger import monitor_logger as logger
from rhubarbe.node import Node
from rhubarbe.ssh import SshProxy

###
# the channel that allows to fuse the leases acquisition cycle
# not too clean of course...
back_channel = Config().value('monitor', 'sidecar_channel_leases_request')

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
        # internal stuff
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
            channel = back_channel
            def closure(*args):
                return self.on_channel(channel, *args)
            self.socketio.on(channel, closure)
        except:
            logger.warn("Connection lost to {}".format(self))
            self.socketio = None

    def on_channel(self, channel, *args):
        # not supposed to run - should be redefined
        logger.warning("ReconnectableSocketIO.on_channel, channel={}, args={}".format(channel, args))

    def emit_info(self, channel, info):
        if self.debug:
            if 'id' in info:
                logger.info("{} emitting id={} : {}"
                            .format(channel, info['id'], one_char_summary(info)))
            else:
                logger.info("{} emitting {}"
                            .format(channel, info))
                            
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
            
    def wait(self, wait):
        if self.socketio:
            try:
                self.socketio.wait(wait)
            except:
                pass

    @staticmethod
    def callback(*args, **kwds):
        logger.info('on socketIO response args={} kwds={}'.format(args, kwds))

class ReconnectableSocketIOMonitor(ReconnectableSocketIO):
    """
    A ReconnectableSocketIO 
    with a back link to a Monitor object, 
    that can receive the 'on_channel' method
    """

    def __init__(self, monitor, *args, **kwds):
        self.monitor = monitor
        ReconnectableSocketIO.__init__(self, *args, **kwds)

    def on_channel(self, channel, *args):
        """
        triggers on_channel back on monitor object
        """
        #print("ReconnectableSocketIOMonitor.on_channel, channel={}, args={}".format(channel, args))
        self.monitor.on_channel(channel, *args)

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
    
    def __init__(self, node, reconnectable, channel, report_wlan=True, debug=False):
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
        #print(self.info)
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
    # 2016-05-28@08:20 - node fit38 - image oai-enb-base2 - by root
    rhubarbe_image_matcher = re.compile("\A/etc/rhubarbe-image:" + \
                                        ".* - image (?P<image_radical>[^ ]+) - by"
                                        )
    rxtx_matcher = re.compile("==> /sys/class/net/wlan(?P<wlan_no>[0-9])/statistics/(?P<rxtx>[rt]x)_bytes <==")
    number_matcher = re.compile("\A[0-9]+\Z")

    def parse_ssh_probe_output(self, stdout, padding_dict):
        flavour = "other"
        extension = ""
        rxtx_dict = {}
        rxtx_key = None
        image_radical = ""
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
            match = self.rhubarbe_image_matcher.match(line)
            if match:
                image_radical = match.group('image_radical')
                continue
            match = self.rxtx_matcher.match(line)
            if match:
                # use a tuple as the hash
                rxtx_key = (match.group('wlan_no'), match.group('rxtx'))
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
            wlan_no, rxtx = rxtx_key
            # rather dirty hack for images that use wlan2 and wlan3
            # expose in wlan0 or wlan1 depending on parity of actual device
            try:
                wlan_no = int(wlan_no)
                wlan_no = wlan_no % 2
            except:
                pass
            if self.debug:
                logger.info("node={self.node} collected {bytes} for device wlan{wlan_no} in {rxtx}"
                            .format(**locals()))
            # do we have something on this measurement ?
            if rxtx_key in self.history:
                previous_bytes, previous_time = self.history[rxtx_key]
                info_key = "wlan_{wlan_no}_{rxtx}_rate".format(**locals())
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
        self.set_info({'os_release' : os_release, 'image_radical' : image_radical },
                      padding_dict, wlan_info_dict)

    async def probe(self, ping_timeout, ssh_timeout):
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
        status = await self.node.get_status()
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
            # this trick allows to have the filename on each output line
            "grep . /etc/rhubarbe-image /dev/null",
            ]
        if self.report_wlan:
            remote_commands.append(
                "head /sys/class/net/wlan?/statistics/[rt]x_bytes"                
            )
        ssh = SshProxy(self.node)
        if self.debug: logger.info("trying to ssh-connect")
        try:
            connected = await asyncio.wait_for(ssh.connect(), timeout=ssh_timeout)
        except asyncio.TimeoutError as e:
            connected = False
        if self.debug: logger.info("connected={}".format(connected))
        if connected:
            try:
                command = ";".join(remote_commands)
                output = await ssh.run(command)
                self.parse_ssh_probe_output(output, padding_dict)
                # required as otherwise we leak openfiles
                try:
                    await ssh.close()
                except:
                    pass
                self.report_info()
                return
            except:
                pass
        else:
            self.set_info({'control_ssh': 'off'})
        if self.debug: logger.info("entering pass3, info={}".format(self.info))
        # pass3 : node is ON but could not ssh
        # check for ping
        # I don't know of an asyncio library to deal with icmp
        # so let's use asyncio.subprocess
        # xxx maybe a Ping class would be the way to go
        control = self.node.control_hostname()
        command = [ "ping", "-c", "1", "-t", "1", control ]
        try:
            subprocess = await asyncio.create_subprocess_exec(
                *command,
                stdout = asyncio.subprocess.DEVNULL,
                stderr = asyncio.subprocess.DEVNULL)
            retcod = await asyncio.wait_for(subprocess.wait(), timeout=ping_timeout)
            self.set_info_and_report({'control_ping' : 'on'})
            return
        except asyncio.TimeoutError as e:
            self.set_info_and_report({'control_ping' : 'off'})
            return

    async def probe_forever(self, cycle, ping_timeout, ssh_timeout):
        """
        runs forever, wait <cycle> seconds between 2 runs of probe()
        """
        while True:
            await self.probe(ping_timeout, ssh_timeout)
            await asyncio.sleep(cycle)
            

from .leases import Lease, Leases

class MonitorLeases:
    def __init__(self, message_bus, reconnectable, channel, cycle, step, wait, debug):
        self.message_bus = message_bus
        self.reconnectable = reconnectable
        self.channel = channel
        self.cycle = float(cycle)
        self.step = float(step)
        self.wait = float(wait)
        self.debug = debug

    def on_back_channel(self, *args):
        # when anything is received on the backchannel, we just go to fast track
        logger.info("MonitorLeases.on_back_channel, args={}".format(args))
        self.fast_track = True

    async def run_forever(self):
        leases = Leases(self.message_bus)
        while True:
            #print("entering")
            self.fast_track = False
            trigger = time.time() + self.cycle
            # check for back_channel every 15 ms
            while not self.fast_track and time.time() < trigger:
                #print("sleeping {}".format(self.step))
                await asyncio.sleep(self.step)
                # give a chance to socketio events to trigger
                self.reconnectable.wait(self.wait)
                
            if self.debug: logger.info("acquiring")
            try:
                await leases.refresh()
                omf_leases = leases.resources
                self.reconnectable.emit_info(self.channel, omf_leases)
                logger.info("advertising {} leases".format(len(omf_leases)))
                if self.debug:
                    logger.info("Leases details: {}".format(omf_leases))
            except Exception as e:
                logger.exception("monitor could not get leases")
            
class Monitor:
    def __init__(self, cmc_names, message_bus, cycle, report_wlan=True,
                 sidecar_hostname=None, sidecar_port=None, debug=False):
        self.cycle = cycle
        self.report_wlan = report_wlan
        self.debug = debug

        # get miscell config
        self.ping_timeout = float(Config().value('networking', 'ping_timeout'))
        self.ssh_timeout = float(Config().value('networking', 'ssh_timeout'))
        self.log_period = float(Config().value('monitor', 'log_period'))

        # socket IO pipe
        hostname = sidecar_hostname or Config().value('monitor', 'sidecar_hostname')
        port = int(sidecar_port or Config().value('monitor', 'sidecar_port'))
        reconnectable = ReconnectableSocketIOMonitor(self, hostname, port, debug)

        # the nodes part
        channel = Config().value('monitor', 'sidecar_channel_status')
        nodes = [ Node (cmc_name, message_bus) for cmc_name in cmc_names ]
        self.monitor_nodes = [
            MonitorNode (node, reconnectable, channel, debug=debug, report_wlan = self.report_wlan)
            for node in nodes]

        # the leases part
        cycle = Config().value('monitor', 'cycle_leases')
        step = Config().value('monitor', 'step_leases')
        wait = Config().value('monitor', 'wait_leases')
        channel = Config().value('monitor', 'sidecar_channel_leases')
        self.monitor_leases = MonitorLeases(
            message_bus, reconnectable, channel, cycle,
            step=step, wait=wait, debug=debug)

    def on_channel(self, channel, *args):
        if channel == back_channel:
            self.monitor_leases.on_back_channel(*args)
        else:
            logger.warning("received data {} on unexpected channel {}".format(channel))

    # xxx no way to select/disable the 2 components (nodes and leases) for now
    async def run(self):
        logger.info("Starting monitor on {} nodes - report_wlan={}"
                    .format(len(self.monitor_nodes), self.report_wlan))
        return asyncio.gather(
            self.monitor_leases.run_forever(),
            *[monitor_node.probe_forever(self.cycle,
                                         ping_timeout = self.ping_timeout,
                                         ssh_timeout = self.ssh_timeout)
              for monitor_node in self.monitor_nodes]
        )

    async def log(self):
        while True:
            line = "".join([one_char_summary(mnode.info) for mnode in self.monitor_nodes])
            logger.info(line)
            await asyncio.sleep(self.log_period)
            

if __name__ == '__main__':
    import sys
    from node import Node
    rebootnames = sys.argv[1:]
    message_bus = asyncio.Queue()
    cycle = Config().value('monitor', 'cycle')
    monitor = Monitor(rebootnames, message_bus, cycle=2, report_wlan=True)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(monitor.run(), monitor.report()))
