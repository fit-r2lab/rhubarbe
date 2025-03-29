"""
The logic for loading an image on a set of nodes
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111

import asyncio

from asynciojobs import Scheduler, Job

from rhubarbe.frisbeed import Frisbeed
from rhubarbe.leases import Leases
from rhubarbe.config import Config


class ImageLoader:

    def __init__(self, nodes, image, bandwidth,         # pylint: disable=r0913
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
        await asyncio.gather(*[node.reboot_on_frisbee(idle)
                               for node in self.nodes])


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
        ipaddr, port = await self.start_frisbeed()
        results = await asyncio.gather(*[node.run_frisbee(ipaddr, port, reset)
                                         for node in self.nodes])
        # we can now kill the server
        self.frisbeed.stop_nowait()
        result = all(results)
        if not result:
            await self.feedback(
                'info',
                "at least one node failed to write that image on disk")
        return result


    # this is synchroneous
    def nextboot_cleanup(self):
        """
        Remove nextboot symlinks for all nodes in this selection
        so next boot will be off the harddrive
        """
        for node in self.nodes:
            node.manage_nextboot_symlink('harddrive')


    async def run(self, reset):
        leases = Leases(self.message_bus)
        await self.feedback('authorization', 'checking for a valid lease')
        valid = await leases.booked_now_by_me()
        if not valid:
            await self.feedback('authorization',
                                "Access refused : you have no lease "
                                "on the testbed at this time")
            return False
        await self.feedback('authorization', 'access granted')
        await (self.stage1()
               if reset
               else self.feedback('info', "Skipping stage1"))
        return await self.stage2(reset)


    def cleanup(self):
        if self.frisbeed:
            self.frisbeed.stop_nowait()
        self.nextboot_cleanup()
        self.display.epilogue()


    def main(self, reset, timeout):

        mainjob = Job(self.run(reset), critical=True)
        displayjob = Job(self.display.run(), forever=True, critical=True)
        scheduler = Scheduler(mainjob, displayjob,
                              timeout=timeout,
                              critical=False)

        try:
            is_ok = scheduler.run()
            if not is_ok:
                scheduler.debrief(silence_done_jobs=True)
                self.display.set_goodbye(
                    f"rhubarbe-load failed: {scheduler.why()}")
                return 1
            return 0 if mainjob.result() else 1
        except KeyboardInterrupt:
            self.display.set_goodbye(
                "rhubarbe-load : keyboard interrupt - exiting")
            return 1
        finally:
            self.cleanup()
