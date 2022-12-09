# pylint: disable=c0111, w1202

import time
import asyncio

from rhubarbe.logger import monitor_logger as logger
from rhubarbe.config import Config
from rhubarbe.leases import Leases

from rhubarbe.monitor.reconnectable import ReconnectableSidecar

class MonitorLeases:                                    # pylint: disable=r0902

    def __init__(self, message_bus, sidecar_url,        # pylint: disable=r0913
                 verbose=False):
        self.message_bus = message_bus
        self.sidecar_url = sidecar_url
        self.verbose = verbose

        # websockets
        self.reconnectable = \
            ReconnectableSidecar(sidecar_url, 'leases')

        self.cycle = float(Config().value('monitor', 'cycle_leases'))
        self.step = float(Config().value('monitor', 'step_leases'))


    def on_back_channel(self, umbrella):
        # when anything is received on the backchannel, we go to fast track
        logger.info(f"MonitorLeases.on_back_channel, umbrella={umbrella}")
        self.fast_track = True                          # pylint: disable=w0201


    async def mainloop(self):
        leases = Leases(self.message_bus)
        if self.verbose:
            logger.info("Entering monitor on leases")
        while True:
            self.fast_track = False                     # pylint: disable=w0201
            trigger = time.time() + self.cycle
            # check for back_channel every 50 ms
            while not self.fast_track and time.time() < trigger:
                await asyncio.sleep(self.step)

            try:
                if self.verbose:
                    logger.info("monitorleases mainloop")
                await leases.refresh()
                # xxx this is fragile
                omf_leases = leases.resources
                logger.info(f"advertising {len(omf_leases)} leases")
                await self.reconnectable.emit_infos(omf_leases)
                if self.verbose:
                    logger.info(f"Leases details: {omf_leases}")
            except Exception:
                logger.exception("monitornodes could not get leases")

    async def run_forever(self):
        def closure(umbrella):
            return self.on_back_channel(umbrella)
        await asyncio.gather(
            self.mainloop(),
            self.reconnectable.keep_connected(),
            self.reconnectable.watch_back_channel('leases', closure)
        )
