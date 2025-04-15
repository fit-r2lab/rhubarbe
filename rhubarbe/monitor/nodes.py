"""
The nodes monitor cyclically checks for the status of all nodes,
and reports it to the sidecar service
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return

# pylint: disable=fixme,logging-fstring-interpolation,missing-function-docstring

import re
import asyncio

from rhubarbe.config import Config
from rhubarbe.node import Node
from rhubarbe.ssh import SshProxy
# use a dedicated logger for monitors
from rhubarbe.logger import monitor_logger as logger

# connect to sidecar
from rhubarbe.monitor.reconnectable import ReconnectableSidecar

# translate info into a single char for logging
def one_char_summary(info):
    if 'cmc_on_off' in info and info['cmc_on_off'] != 'on':
        return '.'
    if 'control_ping' in info and info['control_ping'] != 'on':
        return 'o'
    if 'control_ssh' in info and info['control_ssh'] != 'on':
        return '0'
    if 'os_release' in info and 'fedora' in info['os_release']:
        return 'F'
    if 'os_release' in info and 'ubuntu' in info['os_release']:
        return 'U'
    return '^'


class MonitorNode:
    """
    the logic for probing a node as part of monitoring
    formerly in fitsophia/website/monitor.py
    . first probes for the CMC status; if off: done
    . second probe for ssh; if on : done
    . third, checks for ping
    """

    def __init__(self, node, reconnectable,             # pylint: disable=r0913
                 verbose=False):
        # a rhubarbe.node.Node instance
        self.node = node
        self.reconnectable = reconnectable
        self.verbose = verbose
        # current info - will be reported to sidecar
        self.info = {'id': node.id}

    def set_info(self, *overrides):
        """
        update self.info with all the dicts in overrides
        """
        for override in overrides:
            self.info.update(override)

    async def report_info(self):
        """
        Send info to sidecar
        """
        await self.reconnectable.emit_info(self.info)

    async def set_info_and_report(self, *overrides):
        """
        Convenience to set info and immediately report
        """
        self.set_info(*overrides)
        await self.report_info()


    ubuntu_matcher = re.compile(r"DISTRIB_RELEASE=(?P<ubuntu_version>[0-9.]+)")
    fedora_matcher = re.compile(r"Fedora release (?P<fedora_version>\d+)")
    centos_matcher = re.compile(r"CentOS Linux release (?P<centos_version>\d+)")
    gnuradio_matcher = re.compile(
        r"\AGNURADIO:(?P<gnuradio_version>[0-9\.]+)\Z")
    uname_matcher = re.compile(r"\AUNAME:(?P<uname>.+)\Z")
    # 2016-05-28@08:20 - node fit38 - image oai-enb-base2 - by root
    rhubarbe_image_matcher = re.compile(
        r"\A/etc/rhubarbe-image:.* - image (?P<image_radical>[^ ]+) - by")
    docker_matcher = re.compile(
        r"\ADOCKER:Docker version (?P<docker_version>[0-9\.]+),")
    container_matcher = re.compile(
        r"\ACONTAINER:(?P<running>(true|false)) (?P<image>.*)")


    def parse_ssh_probe_output(self,      # pylint: disable=r0912, r0914, r0915
                               stdout, padding_dict):
        os_release = "other"
        gnuradio_release = "none"
        uname = ""
        image_radical = ""
        docker_version = ""
        container_running = ""
        container_image = ""
        for line in stdout.split("\n"):
            match = self.ubuntu_matcher.match(line)
            if match:
                version = match.group('ubuntu_version')
                os_release = f"ubuntu-{version}"
                continue
            match = self.fedora_matcher.match(line)
            if match:
                version = match.group('fedora_version')
                os_release = f"fedora-{version}"
                continue
            match = self.centos_matcher.match(line)
            if match:
                version = match.group('centos_version')
                os_release = f"centos-{version}"
                continue
            match = self.gnuradio_matcher.match(line)
            if match:
                gnuradio_release = match.group('gnuradio_version')
                continue
            match = self.uname_matcher.match(line)
            if match:
                uname = match.group('uname')
                continue
            match = self.rhubarbe_image_matcher.match(line)
            if match:
                image_radical = match.group('image_radical')
                continue
            if match := self.docker_matcher.match(line):
                docker_version = match.group('docker_version')
                continue
            if match := self.container_matcher.match(line):
                container_running = match.group('running')
                container_image = match.group('image')
                continue

        # xxx would make sense to clean up history for measurements that
        # we were not able to collect at this cycle
        self.set_info({
            'os_release': os_release,
            'gnuradio_release': gnuradio_release,
            'uname': uname,
            'image_radical': image_radical,
            'docker_version': docker_version,
            'container_running': container_running,
            'container_image': container_image,
            }, padding_dict)

    async def probe(self,                 # pylint: disable=r0912, r0914, r0915
                    ping_timeout, ssh_timeout):
        """
        The logic for getting one node's info and send it to sidecar
        """
        if self.verbose:
            logger.info(f"entering pass1, info={self.info}")
        # pass1 : check for status
        padding_dict = {
            'control_ping': 'off',
            'control_ssh': 'off',
            # don't overwrite os_release though
        }
        # get USRP status no matter what - use "" if we receive None
        # to limit noise when the node is physically removed
        usrp_status = await self.node.get_usrpstatus() or 'fail'
        # replace usrpon and usrpoff with just on and off
        self.set_info({'usrp_on_off': usrp_status.replace('usrp', '')})
        # get CMC status
        status = await self.node.get_status()
        if status == "off":
            await self.set_info_and_report({'cmc_on_off': 'off'}, padding_dict)
            return
        elif status != "on":
            await self.set_info_and_report({'cmc_on_off': 'fail'}, padding_dict)
            return
        if self.verbose:
            logger.info(f"entering pass2, info={self.info}")
        # pass2 : CMC status is ON - let's try to ssh it
        self.set_info({'cmc_on_off': 'on'})
        padding_dict = {
            'control_ping': 'on',
            'control_ssh': 'on',
        }

        remote_commands = [
            "cat /etc/lsb-release /etc/redhat-release /etc/gnuradio-release "
            "2> /dev/null | grep -i release",
            "echo -n GNURADIO: ; gnuradio-config-info --version "
            "2> /dev/null || echo none",
            # this trick allows to have the filename on each output line
            "grep . /etc/rhubarbe-image /dev/null",
            "echo -n UNAME: ; uname -r",
            "echo -n DOCKER: ; docker --version",
            "echo -n CONTAINER: ; docker inspect "
                "--format='{{.State.Running}} {{.Config.Image}}' container",
            ]
        # reconnect each time
        self.set_info({'control_ssh': 'off'})
        async with SshProxy(self.node) as ssh:
            logger.info(f"trying to ssh-connect to {self.node.control_hostname()} "
                        f"(timeout={ssh_timeout})")
            try:
                connected = await asyncio.wait_for(
                    ssh.connect(), timeout=ssh_timeout)
            except asyncio.TimeoutError:
                connected = False
                self.set_info({'control_ssh': 'off'})
            logger.info(f"{self.node.control_hostname()} ssh-connected={connected}")
            if connected:
                self.set_info({'control_ssh': 'on'})
                try:
                    command = ";".join(remote_commands)
                    output = await asyncio.wait_for(
                        ssh.run(command), timeout=ssh_timeout)
                    logger.debug(f"{output=:20s}...")
                    # padding dict here sets control_ssh and control_ping to on
                    self.parse_ssh_probe_output(output, padding_dict)
                    # required as otherwise we leak openfiles
                    try:
                        await ssh.close()
                    except Exception:                           # pylint: disable=broad-except
                        logger.exception("monitornodes oops 1")
                except asyncio.TimeoutError:
                    logger.info(f"received ssh timeout with {self.node.control_hostname()}")
                    self.set_info({'control_ssh': 'off'})
                except Exception:                                   # pylint: disable=broad-except
                    logger.exception("monitornodes remote_command failed")
        logger.info(f"{self.node.control_hostname()} ssh-based logic done "
                    f"ssh is deemed {self.info['control_ssh']}")

        # if we could ssh then we're done
        if self.info['control_ssh'] == 'on':
            await self.report_info()
            return

        logger.info(f"entering pass3, info={self.info}")
        # pass3 : node is ON but could not ssh
        # check for ping
        # I don't know of an asyncio library to deal with icmp
        # so let's use asyncio.subprocess
        # xxx maybe a Ping class would be the way to go
        control = self.node.control_hostname()
        command = ["ping", "-c", "1", "-t", "1", control]
        try:
            subprocess = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            # failure occurs through timeout
            await asyncio.wait_for(subprocess.wait(),
                                   timeout=ping_timeout)
            await self.set_info_and_report({'control_ping': 'on'})
            return
        except asyncio.TimeoutError:
            await self.set_info_and_report({'control_ping': 'off'})
            return

    async def probe_forever(self, cycle, ping_timeout, ssh_timeout):
        """
        runs forever, wait <cycle> seconds between 2 runs of probe()
        """
        while True:
            try:
                await self.probe(ping_timeout, ssh_timeout)
            except Exception:                           # pylint: disable=broad-except
                logger.exception("monitornodes oops 2")
            await asyncio.sleep(cycle)


class MonitorNodes:
    """
    monitor all nodes and reports their status to the sidecar service
    """

    def __init__(self, cmc_names, message_bus,
                 sidecar_url, cycle, verbose=False):
        self.cycle = cycle
        self.verbose = verbose

        # get miscell config
        self.ping_timeout = float(Config().value('networking', 'ping_timeout'))
        self.ssh_timeout = float(Config().value('networking', 'ssh_timeout'))
        self.log_period = float(Config().value('monitor', 'log_period'))

        # websockets
        self.reconnectable = \
            ReconnectableSidecar(sidecar_url, 'nodes')

        # the nodes part
        nodes = [Node(cmc_name, message_bus) for cmc_name in cmc_names]
        self.monitor_nodes = [
            MonitorNode(node=node, reconnectable=self.reconnectable,
                        verbose=verbose)
            for node in nodes]

    async def log(self):
        previous = 0
        while True:
            line = "".join([one_char_summary(mnode.info)
                            for mnode in self.monitor_nodes])
            current = self.reconnectable.counter
            delta = f"+ {current-previous}"
            line += f" {current} emits ({delta})"
            previous = current
            logger.warning(line)
            await asyncio.sleep(self.log_period)

    async def run_forever(self):
        logger.info(f"Starting nodes on {len(self.monitor_nodes)} nodes")
        return asyncio.gather(
            *[monitor_node.probe_forever(self.cycle,
                                         ping_timeout=self.ping_timeout,
                                         ssh_timeout=self.ssh_timeout)
              for monitor_node in self.monitor_nodes],
            self.reconnectable.keep_connected(),
            self.log(),
        )
