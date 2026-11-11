"""
The nodes monitor cyclically checks for the status of all nodes,
and reports it to the sidecar service
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return

# pylint: disable=fixme,logging-fstring-interpolation,missing-function-docstring

import errno
import re
import time
import asyncio

from rhubarbe.config import Config
from rhubarbe.inventorynodes import InventoryNodes
from rhubarbe.node import Node
from rhubarbe.r2labapiproxy import R2labApiProxy
from rhubarbe.ssh import SshProxy
# use a dedicated logger for monitors
from rhubarbe.logger import monitor_logger as logger

# connect to sidecar
from rhubarbe.monitor.reconnectable import ReconnectableSidecar
from rhubarbe.monitor.watchdog import Watchdog, count_open_files

# translate info into a single char for logging
def one_char_summary(info):
    if 'cmc_on_off' in info and info['cmc_on_off'] != 'on':
        return '.'
    if 'control_ping' in info and info['control_ping'] != 'on':
        return 'o'
    if 'control_ssh' in info and info['control_ssh'] != 'on':
        return '0'
    # what the user runs on: the container if any, otherwise the metal
    os_release = ((info.get('container') or {}).get('os_release')
                  or (info.get('metal') or {}).get('os_release') or '')
    if 'fedora' in os_release:
        return 'F'
    if 'ubuntu' in os_release:
        return 'U'
    return '^'


async def ping(hostname, timeout):
    """
    Returns True if hostname answers to a single ping within timeout seconds

    I don't know of an asyncio library to deal with icmp,
    so let's use asyncio.subprocess; the options are kept to the bare minimum
    as the ones that deal with timeouts differ between linux and macos;
    we enforce the timeout ourselves, and make sure to kill the child
    """
    process = await asyncio.create_subprocess_exec(
        "ping", "-c", "1", hostname,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        # a ping that fails fast (unreachable, unknown host) is a failure too
        return await asyncio.wait_for(process.wait(), timeout) == 0
    except asyncio.TimeoutError:
        return False
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


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
        self.id = node.id
        self.refresh_static()
        # for the watchdog
        self.last_probe_end = time.time()
        # what the last port-22 probe found, None if it failed
        self._probe22 = None
        # see probe_setup
        self._regular_cycles = 0

    def refresh_static(self):
        """
        the 'static' record comes from the inventory, and is exposed as-is;
        if it vanishes from the inventory we report None so that
        the sidecar forgets it
        """
        static = InventoryNodes().static_info(self.node.cmc_name)
        if static:
            self.info['static'] = static
        elif self.info.get('static'):
            self.info['static'] = None

    def set_availability(self, record):
        """
        record is what we got from r2labapi for this node, or None if it
        has none, in which case the node is available; we expose the status,
        and the reason and since when known, but not who set it
        """
        record = record or {}
        self.info['availability'] = {
            key: record[key] for key in ('status', 'reason', 'since')
            if record.get(key)} or {'status': 'ok'}

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
        r"\ADOCKER:(?:Docker|podman) version (?P<docker_version>[0-9\.]+)")
    container_matcher = re.compile(
        r"\ACONTAINER:(?P<running>(true|false)) (?P<image>.*)")
    # from /etc/os-release, see remote_commands
    osr_matcher = re.compile(
        r"\AOSR:(?P<key>ID|VERSION_ID|PRETTY_NAME)="
        r"[\"']?(?P<value>.*?)[\"']?\Z")
    # ISO timestamp, as in 2026-11-07T09:12:01.123456789Z
    started_matcher = re.compile(r"\ASTARTED:(?P<started>\d{4}-.*Z)\Z")


    def parse_docker_output(self, stdout):
        """
        the docker-related fields found in the output of the DOCKER: and
        CONTAINER: commands; empty strings for what is missing
        """
        info = {'docker_version': "", 'container_running': "",
                'container_image': "", 'container_started': ""}
        for line in stdout.split("\n"):
            if match := self.docker_matcher.match(line):
                info['docker_version'] = match.group('docker_version')
            elif match := self.container_matcher.match(line):
                info['container_running'] = match.group('running')
                info['container_image'] = match.group('image')
            elif match := self.started_matcher.match(line):
                info['container_started'] = match.group('started')
        return info

    # what we ask a host about itself, on port 22 or on the docker host port
    os_commands = [
        "cat /etc/lsb-release /etc/redhat-release /etc/gnuradio-release "
        "2> /dev/null | grep -i release",
        "grep -E '^(ID|VERSION_ID|PRETTY_NAME)=' /etc/os-release 2> /dev/null "
        "| sed 's/^/OSR:/'",
        "echo -n GNURADIO: ; gnuradio-config-info --version "
        "2> /dev/null || echo none",
        # this trick allows to have the filename on each output line
        "grep . /etc/rhubarbe-image /dev/null",
        "echo -n UNAME: ; uname -r",
    ]
    docker_commands = [
        "echo -n DOCKER: ; docker --version",
        "echo -n CONTAINER: ; docker inspect "
        "--format='{{.State.Running}} {{.Config.Image}}' container",
        "echo -n STARTED: ; docker inspect "
        "--format='{{.State.StartedAt}}' container",
    ]

    async def probe_docker_host(self, ssh_timeout):
        """
        On a node that runs a container, port 22 leads into the container,
        where there is no docker; the host has its own sshd on another port
        ([docker] host_ssh_port), that we use to learn about the metal, docker
        and the container.
        returns (outcome, result) where outcome is
        . 'docker': the host answered and has docker; result is
          {'port', 'os', 'docker'}, as parse_probe_output
        . 'refused': nothing listens there (or a firewall rejects us),
          so this is not a docker host
        . 'unknown': we could not tell (timeout, no docker, any failure)
        and result is None unless the outcome is 'docker'
        """
        try:
            port = int(Config().value('docker', 'host_ssh_port'))
        except Exception:                               # pylint: disable=w0703
            port = 2222
        name = self.node.control_hostname()
        # a plain tcp connection tells 'refused' from the rest
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.node.control_hostname(), port),
                timeout=ssh_timeout)
            writer.close()
        except ConnectionRefusedError:
            return 'refused', None
        except OSError as exc:
            # a firewall that rejects with 'host prohibited', as fedora does,
            # shows up as 'no route to host': same thing as refused, as we
            # only get there when port 22 has just answered
            if exc.errno == errno.EHOSTUNREACH:
                return 'refused', None
            return 'unknown', None
        except Exception:                               # pylint: disable=w0703
            return 'unknown', None
        async with SshProxy(self.node, port=port) as ssh:
            try:
                if not await asyncio.wait_for(
                        ssh.connect(), timeout=ssh_timeout):
                    return 'unknown', None
                output = await asyncio.wait_for(
                    ssh.run(";".join(self.os_commands
                                     + self.docker_commands)),
                    timeout=ssh_timeout)
                result = self.parse_probe_output(output or "", port)
                if not result['docker']['docker_version']:
                    return 'unknown', None
                logger.info(
                    f"{name}: docker host probe on port {port} -> "
                    f"{result['docker']['docker_version']!r}")
                return 'docker', result
            except asyncio.TimeoutError:
                logger.info(f"{name}: docker host probe timeout")
            except Exception:                           # pylint: disable=w0703
                logger.exception(f"{name}: docker host probe failed")
        return 'unknown', None

    # how many cycles in a row a node must look like a regular one (nothing
    # on the docker host port) before we forget its docker info; shields from
    # the transitional probes when a docker node reboots
    REGULAR_CYCLES = 2

    # the sidecar broadcasts and stores a node whenever something changes, so
    # 'seen' must not change at every cycle: it is refreshed only when the
    # record changes, or after this many seconds
    SEEN_REFRESH = 300

    def with_seen(self, key, record):
        """
        sets record['seen'], keeping the previous value if the rest of
        the record did not change and is recent enough
        """
        if record is None:
            return None
        previous = self.info.get(key)
        if (previous and previous.get('seen')
                and time.time() - previous['seen'] < self.SEEN_REFRESH
                and {k: v for k, v in previous.items() if k != 'seen'}
                == {k: v for k, v in record.items() if k != 'seen'}):
            record['seen'] = previous['seen']
        return record

    def metal_record(self, result):
        """the 'metal' sub-record, from a parsed probe of the bare machine"""
        os_info, docker = result['os'], result['docker']
        return self.with_seen('metal', {
            'ssh_port': result['port'],
            'os_release': os_info['os_release'],
            'pretty_name': os_info['pretty_name'],
            'image_radical': os_info['image_radical'],
            'uname': os_info['uname'],
            'gnuradio_release': os_info['gnuradio_release'],
            'docker_version': docker['docker_version'],
            'seen': int(time.time()),
        })

    def container_record(self, docker, probe22):
        """
        the 'container' sub-record, or None if the metal has no container;
        the os_release is only visible from inside, through port 22; when
        that is not available we keep the one we knew, if for the same image
        """
        image = docker['container_image']
        if not image:
            return None
        if probe22:
            os_release = probe22['os']['os_release']
            pretty_name = probe22['os']['pretty_name']
        else:
            previous = self.info.get('container') or {}
            same = previous.get('image') == image
            os_release = previous.get('os_release') if same else None
            pretty_name = previous.get('pretty_name') if same else None
        return self.with_seen('container', {
            'image': image,
            'running': docker['container_running'] == 'true',
            'started': docker['container_started'],
            'os_release': os_release,
            'pretty_name': pretty_name,
            'seen': int(time.time()),
        })

    async def probe_setup(self, ssh_timeout):
        """
        what runs on the node: the 'metal' and 'container' sub-records

        These are only written when what we saw is conclusive: whatever a
        partial probe could not see keeps its last value, which is what is
        shown for a node that is off
        """
        probe22 = self._probe22
        if probe22 and probe22['docker']['docker_version']:
            # a docker image whose own sshd is on port 22: no container side
            self.set_info(
                {'metal': self.metal_record(probe22),
                 'container': self.container_record(probe22['docker'], None)})
            self._regular_cycles = 0
            return
        outcome, host = await self.probe_docker_host(ssh_timeout)
        if outcome == 'docker':
            self.set_info(
                {'metal': self.metal_record(host),
                 'container': self.container_record(host['docker'], probe22)})
            # we have just ssh-ed the node, if only on the host port: this is
            # all there is to see on a docker node that has no container
            self.set_info({'control_ssh': 'on', 'control_ping': 'on'})
            self._regular_cycles = 0
        elif outcome == 'refused' and probe22:
            self._regular_cycles += 1
            # first data we have for this node: no need to wait
            if (self._regular_cycles >= self.REGULAR_CYCLES
                    or 'metal' not in self.info):
                self.set_info(
                    {'metal': self.metal_record(probe22), 'container': None})
        else:
            self._regular_cycles = 0

    def parse_probe_output(self,          # pylint: disable=r0912, r0914, r0915
                           stdout, port):
        """
        parses the output of os_commands + docker_commands, as run on
        some port; returns {'port', 'os': {...}, 'docker': {...}}
        """
        os_release = "other"
        gnuradio_release = "none"
        uname = ""
        image_radical = ""
        osr = {}
        for line in stdout.split("\n"):
            match = self.osr_matcher.match(line.strip())
            if match:
                osr[match.group('key')] = match.group('value')
                continue
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

        # os-release knows about all the distributions; it gives
        # the same names as above for ubuntu, fedora and centos,
        # e.g. alpine-3.22.0, debian-12, arch (rolling, no version)
        if osr.get('ID'):
            os_release = osr['ID'] + (
                f"-{osr['VERSION_ID']}" if osr.get('VERSION_ID') else "")
        return {
            'port': port,
            'os': {'os_release': os_release,
                   'pretty_name': osr.get('PRETTY_NAME', ""),
                   'gnuradio_release': gnuradio_release,
                   'uname': uname,
                   'image_radical': image_radical},
            'docker': self.parse_docker_output(stdout),
        }

    def parse_ssh_probe_output(self, stdout, padding_dict):
        """
        the port 22 probe: this may well be in a container, where docker is
        not visible; what is learned about the metal, docker and the image
        is decided in probe_setup, once we know where we are
        """
        self._probe22 = self.parse_probe_output(stdout, 22)
        # padding_dict is control_ssh and control_ping on
        self.set_info(padding_dict)

    async def probe(self,                 # pylint: disable=r0912, r0914, r0915
                    ping_timeout, ssh_timeout):
        """
        The logic for getting one node's info and send it to sidecar
        """
        # pick up a newly published inventory
        InventoryNodes().reload_if_changed()
        self.refresh_static()
        if self.verbose:
            logger.info(f"entering pass1, info={self.info}")
        # pass1 : check for status
        padding_dict = {
            'control_ping': 'off',
            'control_ssh': 'off',
            # don't overwrite metal and container though
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

        remote_commands = self.os_commands + self.docker_commands
        # reconnect each time
        self.set_info({'control_ssh': 'off'})
        self._probe22 = None
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
                    if output is None:
                        # ssh.run() has already logged the reason
                        self.set_info({'control_ssh': 'off'})
                    else:
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
        # in a container, docker can only be seen from the host
        await self.probe_setup(ssh_timeout)
        logger.info(f"{self.node.control_hostname()} ssh-based logic done "
                    f"ssh is deemed {self.info['control_ssh']}")

        # if we could ssh then we're done
        if self.info['control_ssh'] == 'on':
            await self.report_info()
            return

        logger.info(f"entering pass3, info={self.info}")
        # pass3 : node is ON but could not ssh
        # check for ping
        control = self.node.control_hostname()
        control_ping = 'on' if await ping(control, ping_timeout) else 'off'
        await self.set_info_and_report({'control_ping': control_ping})

    async def probe_forever(self, cycle, ping_timeout, ssh_timeout):
        """
        runs forever, wait <cycle> seconds between 2 runs of probe()
        """
        while True:
            try:
                await self.probe(ping_timeout, ssh_timeout)
            except Exception:                           # pylint: disable=broad-except
                logger.exception("monitornodes oops 2")
            self.last_probe_end = time.time()
            await asyncio.sleep(cycle)


class MonitorNodes:
    """
    monitor all nodes and reports their status to the sidecar service
    """

    def __init__(self, cmc_names, message_bus,
                 sidecar_url, cycle, verbose=False,
                 stall_timeout=300., stall_check_period=30.):
        self.cycle = cycle
        self.verbose = verbose

        # get miscell config
        self.ping_timeout = float(Config().value('networking', 'ping_timeout'))
        self.ssh_timeout = float(Config().value('networking', 'ssh_timeout'))
        self.log_period = float(Config().value('monitor', 'log_period'))

        self.cycle_availability = float(
            Config().value('monitor', 'cycle_availability'))
        # reading availability needs no token, so we do not use one
        self.api_url = Config().value('r2labapi', 'url')

        # websockets
        self.reconnectable = \
            ReconnectableSidecar(sidecar_url, 'nodes')

        # the nodes part
        nodes = [Node(cmc_name, message_bus) for cmc_name in cmc_names]
        self.monitor_nodes = [
            MonitorNode(node=node, reconnectable=self.reconnectable,
                        verbose=verbose)
            for node in nodes]
        self.watchdog = Watchdog('monitornodes', self.reconnectable,
                                 self.monitor_nodes,
                                 stall_timeout=stall_timeout,
                                 check_period=stall_check_period)

    def apply_availabilities(self, records):
        """
        records: what r2labapi returns, i.e. the nodes that have a record;
        the others are available
        """
        by_id = {record['node_id']: record for record in records}
        for monitor_node in self.monitor_nodes:
            monitor_node.set_availability(by_id.get(monitor_node.id))

    async def poll_availability(self):
        """
        keep the availability of the nodes in sync with r2labapi
        if it cannot be reached we do nothing, so that every node keeps
        the last availability we knew
        """
        proxy = R2labApiProxy(self.api_url)
        while True:
            try:
                records = await asyncio.to_thread(
                    proxy.get_node_availabilities)
                self.apply_availabilities(records)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                    # pylint: disable=w0703
                logger.info(f"r2labapi unreachable ({exc})"
                            f" - keeping availabilities as they were")
            await asyncio.sleep(self.cycle_availability)

    async def log(self):
        previous = 0
        while True:
            line = "".join([one_char_summary(mnode.info)
                            for mnode in self.monitor_nodes])
            current = self.reconnectable.counter
            delta = f"+ {current-previous}"
            line += f" {current} emits ({delta})"
            line += f" - {self.reconnectable} - fds={count_open_files()}"
            previous = current
            logger.warning(line)
            await asyncio.sleep(self.log_period)

    async def run_forever(self):
        logger.info(f"Starting nodes on {len(self.monitor_nodes)} nodes")
        # named tasks, so the watchdog snapshot shows which node is stuck
        probes = [
            asyncio.create_task(
                monitor_node.probe_forever(self.cycle,
                                           ping_timeout=self.ping_timeout,
                                           ssh_timeout=self.ssh_timeout),
                name=f"probe-node-{monitor_node.id}")
            for monitor_node in self.monitor_nodes]
        await asyncio.gather(
            *probes,
            self.reconnectable.keep_connected(),
            self.poll_availability(),
            self.log(),
            self.watchdog.run(),
        )
