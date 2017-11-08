import asyncio

from asynciojobs import Scheduler, Job

import rhubarbe.util as util
from rhubarbe.frisbeed import Frisbeed
from rhubarbe.leases import Leases
from rhubarbe.config import Config

class ImageLoader:

    def __init__(self, nodes,  image, bandwidth,
                 message_bus, display):
        self.nodes = nodes
        self.image = image
        self.bandwidth = bandwidth
        self.display = display
        self.message_bus = message_bus
        #
        self.frisbeed = None

    async def feedback(self, field, msg):
        await self.message_bus.put({field: msg})

    async def stage1(self):
        the_config = Config()
        idle = int(the_config.value('nodes', 'idle_after_reset'))
        await asyncio.gather(*[node.reboot_on_frisbee(idle) for node in self.nodes])

    async def start_frisbeed(self):
        self.frisbeed = Frisbeed(self.image, self.bandwidth, self.message_bus)
        ip_port = await self.frisbeed.start()
        return ip_port

    async def stage2(self, reset):
        """
        wait for all nodes to be telnet-friendly
        then run frisbee in all of them
        and reset the nodes afterwards, unless told otherwise
        """
        # start_frisbeed will return the ip+port to use 
        ip, port = await self.start_frisbeed()
        results = await asyncio.gather(*[node.run_frisbee(ip, port, reset) for node in self.nodes])
        # we can now kill the server
        self.frisbeed.stop_nowait()
        return all(results)

    # this is synchroneous
    def nextboot_cleanup(self):
        """
        Remove nextboot symlinks for all nodes in this selection
        so next boot will be off the harddrive
        """
        [node.manage_nextboot_symlink('harddrive') for node in self.nodes]

    async def run(self, reset):
        leases = Leases(self.message_bus)
        await self.feedback('authorization','checking for a valid lease')
        valid = await leases.booked_now_by_me()
        if not valid:
            await self.feedback('authorization',
                                "Access refused : you have no lease on the testbed at this time")
            return False
        else:
            await self.feedback('authorization','access granted')
            await (self.stage1() if reset else self.feedback('info', "Skipping stage1"))
            return await (self.stage2(reset))

    def main(self, reset, timeout):

        mainjob = Job(self.run(reset), critical=True)
        displayjob = Job(self.display.run(), forever=True, critical=True)
        scheduler = Scheduler (mainjob, displayjob)

        try:
            ok = scheduler.orchestrate(timeout = timeout)
            if not ok:
                scheduler.debrief()
                self.display.set_goodbye("rhubarbe-load failed: {}".format(scheduler.why()))
                return 1
            return 0 if mainjob.result() else 1
        except KeyboardInterrupt as e:
            self.display.set_goodbye("rhubarbe-load : keyboard interrupt - exiting")
            return 1
        finally:
            self.frisbeed and self.frisbeed.stop_nowait()
            self.nextboot_cleanup()
            self.display.epilogue()
