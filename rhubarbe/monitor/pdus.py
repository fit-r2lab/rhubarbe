"""
The pdus monitor cyclically checks for the status of all pdus,
and reports it to the sidecar service

As a start, the tool only reports ON or OFF or UNKNOWN
"""

# pylint: disable=logging-fstring-interpolation, fixme, missing-function-docstring

import asyncio

from rhubarbe.logger import monitor_logger as logger

from rhubarbe.inventorypdus import InventoryPdus, PduDevice
from rhubarbe.monitor.reconnectable import ReconnectableSidecar


class MonitorPdu:

    """
    monitor one PDU
    """

    def __init__(self, pdu_device: PduDevice,
                 reconnectable, verbose, cycle=2):
        self.pdu_device = pdu_device
        self.reconnectable = reconnectable
        self.cycle = cycle
        self.verbose = verbose
        self.info = {'id': self.name}

    @property
    def name(self):
        return self.pdu_device.name

    def __repr__(self):
        return f"monitored pdu #{self.name}"

    async def emit(self):
        await self.reconnectable.emit_info(self.info)

    async def probe(self):
        # avoid clogging the logs
        status = await self.pdu_device.status(show_stdout=False)
        on_off = 'on' if status == 0 else 'off' if status == 1 else 'unknown'
        self.info['on_off'] = on_off
        if self.verbose:
            logger.info(f"on_off on PDU {self.name} is {on_off}")
        await self.emit()

    async def probe_forever(self):
        while True:
            await self.probe()
            await asyncio.sleep(self.cycle)


class MonitorPdus:
    """
    monitor all phones status and report to the sidecar service
    """

    def __init__(self, verbose, sidecar_url, cycle, names=None):
        self.verbose = verbose

        devices = InventoryPdus.load().devices
        if names:
            devices = [device for device in devices if device.name in names]

        self.reconnectable = ReconnectableSidecar(sidecar_url, 'pdus')
        # xxx this is fragile
        # we rely on the fact that the items in the inventory
        # match the args of MonitorPdu's constructor
        self.pdus = [
                MonitorPdu(
                    pdu_device = device,
                    reconnectable=self.reconnectable,
                    verbose=verbose,
                    cycle=cycle)
                for device in devices
            ]

    async def run_forever(self):
        await asyncio.gather(
            *[pdu.probe_forever()
              for pdu in self.pdus],
            self.reconnectable.keep_connected())
