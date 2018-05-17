"""
The monitor cyclically checks for the status of all nodes,
and reports it to the sidecar service

This simple tool works a bit like rhubarbe.monitor but on the r2lab phones
for now it probes for airplane_mode, but does not probe for the state of
the wifi service because I could not get to work the magic sentences
like 'adb shell svc enable wifi' and similar that I have found on the web

it is a little off or ad hoc wrt to rest of rhubarbe
it does make sense to add it here though, so it can leverage
(*) sidecar_url as defined in config
(*) and ReconnectableSocketIO from monitor
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0703, w1202

import asyncio

from apssh.sshproxy import SshProxy
from apssh.formatters import CaptureFormatter

from rhubarbe.config import Config
from rhubarbe.logger import monitor_logger as logger

from rhubarbe.inventoryphones import InventoryPhones
from rhubarbe.monitor import ReconnectableSocketIO


class MonitorPhone:                                     # pylint: disable=r0902

    # id is what you get through adb devices
    def __init__(self, id,                       # pylint: disable=r0913, w0622
                 gw_host, gw_user, gw_key,
                 adb_bin, adb_id,
                 reconnectable, channel, verbose, cycle=2):
        self.id = id                                    # pylint: disable=c0103
        self.gateway = SshProxy(
            hostname=gw_host,
            username=gw_user,
            keys=[gw_key],
            # setting verbose would end up on stdout
            formatter=CaptureFormatter(verbose=False)
        )
        self.adb_bin = adb_bin
        self.adb_id = adb_id
        self.reconnectable = reconnectable
        self.channel = channel
        self.cycle = cycle
        self.info = {'id': self.id}
        self.verbose = verbose

    def __repr__(self):
        return ("monitored phone #{} via gw {}@{} with key {}"
                .format(self.id, self.gateway.username, self.gateway.hostname,
                        self.gateway.keys[0]))

    def emit(self):
        self.reconnectable.emit_info(self.channel, self.info,
                                     wrap_in_list=True)

    async def probe(self):

        # connect or reconnect if needed
        if not self.gateway.is_connected():
            try:
                await self.gateway.connect_lazy()
                logger.info("Connected -> {}".format(self.gateway))
            except Exception as exc:
                logger.error("Could not connect -> {} (exc={})"
                             .format(self.gateway, exc))
                self.info['airplane_mode'] = 'fail'
                self.emit()

        if not self.gateway.is_connected():
            logger.error("Not connected to gateway - aborting")
            return

        try:
            self.gateway.formatter.start_capture()
            retcod = await self.gateway.run(
                "{} shell \"settings get global airplane_mode_on\""
                .format(self.adb_bin))
            result = self.gateway.formatter.get_capture().strip()
            airplane_mode = 'fail' if retcod != 0 \
                else 'on' if result == '1' else 'off'
            if self.verbose:
                logger.info("probed phone {} : retcod={} result={} "
                            "-> airplane_mode = {}"
                            .format(self.adb_id, retcod, result,
                                    airplane_mode))
            self.info['airplane_mode'] = airplane_mode

        except Exception as exc:
            logger.error("Could not probe {} -> (e={})"
                         .format(self.adb_id, exc))
            self.info['airplane_mode'] = 'fail'
            # force ssh reconnect
            self.gateway.conn = None
        self.emit()

    async def probe_forever(self):
        while True:
            await self.probe()
            await asyncio.sleep(self.cycle)


class MonitorPhones:                                     # pylint:disable=r0903
    def __init__(self, verbose, sidecar_url, cycle):
        self.verbose = verbose
        main_channel = Config().value('sidecar', 'channel_phones')

        phone_specs = InventoryPhones().all_phones()

        reconnectable = ReconnectableSocketIO(sidecar_url)
        # xxx this is fragile
        # we rely on the fact that the items in the inventory
        # match the args of MonitorPhone's constructor
        self.phones = [
            MonitorPhone(reconnectable=reconnectable,
                         channel=main_channel,
                         verbose=verbose,
                         cycle=cycle,
                         **spec)
            for spec in phone_specs]

    async def run(self):
        await asyncio.gather(*[phone.probe_forever()
                               for phone in self.phones])
