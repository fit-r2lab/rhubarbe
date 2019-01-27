# pylint: disable=c0111, w1202

import time
import asyncio

from rhubarbe.logger import monitor_logger as logger
from rhubarbe.config import Config
from rhubarbe.leases import Leases

from rhubarbe.monitor.sidecar import ReconnectableSidecar

class MonitorLeases:                                    # pylint: disable=r0902

    def __init__(self, message_bus, sidecar_url,        # pylint: disable=r0913
                 verbose=False):
        self.message_bus = message_bus
        self.sidecar_url = sidecar_url
        self.verbose = verbose

        # websockets
        print(f"URL={sidecar_url}")
        self.reconnectable = \
            ReconnectableSidecar(sidecar_url, 'leases')

        self.cycle = float(Config().value('monitor', 'cycle_leases'))
        self.step = float(Config().value('monitor', 'step_leases'))
#        self.wait = float(Config().value('monitor', 'wait_leases'))

    def on_back_channel(self, *args):
        # when anything is received on the backchannel, we go to fast track
        logger.info("MonitorLeases.on_back_channel, args={}".format(args))
        self.fast_track = True                          # pylint: disable=w0201

    async def run_forever(self):
        leases = Leases(self.message_bus)
        if self.verbose:
            logger.info("Entering monitor on leases")
        while True:
            self.fast_track = False                     # pylint: disable=w0201
            trigger = time.time() + self.cycle
            # xxx - remove me - tmp ; speed it up
            trigger = time.time() + 10
            # check for back_channel every 15 ms
            while not self.fast_track and time.time() < trigger:
                await asyncio.sleep(self.step)
#                # give a chance to socketio events to trigger
#                self.reconnectable.wait(self.wait)

            try:
                if self.verbose:
                    logger.info("monitorleases mainloop")
                await leases.refresh()
                # xxx this is fragile
                omf_leases = leases.resources
                logger.info("advertising {} leases".format(len(omf_leases)))
                await self.reconnectable.emit_infos(omf_leases)
                if self.verbose:
                    logger.info("Leases details: {}".format(omf_leases))
            except Exception:
                logger.exception("monitornodes could not get leases")
