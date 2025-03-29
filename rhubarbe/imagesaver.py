"""
The logic for saving an image
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111

import os

from asynciojobs import Scheduler, Job

from rhubarbe.collector import Collector
from rhubarbe.leases import Leases
from rhubarbe.config import Config


class ImageSaver:

    def __init__(self, node, image, radical,            # pylint: disable=r0913
                 message_bus, display, comment):
        self.node = node
        self.image = image
        self.radical = radical
        self.message_bus = message_bus
        self.display = display
        self.comment = comment
        #
        self.collector = None


    async def feedback(self, field, msg):
        await self.message_bus.put({field: msg})


    # this is exactly as imageloader
    async def stage1(self):
        the_config = Config()
        idle = int(the_config.value('nodes', 'idle_after_reset'))
        await self.node.reboot_on_frisbee(idle)


    # this is synchroneous
    def nextboot_cleanup(self):
        """
        Remove nextboot symlinks for all nodes in this selection
        so next boot will be off the harddrive
        """
        self.node.manage_nextboot_symlink('harddrive')


    async def start_collector(self):
        self.collector = Collector(self.image, self.message_bus)
        port = await self.collector.start()
        return port


    async def stage2(self, reset):
        """
        run collector (a netcat server)
        then wait for the node to be telnet-friendly,
        then run imagezip on the node
        reset node when finished unless reset is False
        """
        # start_frisbeed will return the ip+port to use
        await self.feedback('info', f"Saving image from {self.node}")
        port = await self.start_collector()
        result = await self.node.run_imagezip(port, reset,
                                              self.radical, self.comment)
        # we can now kill the server
        self.collector.stop_nowait()
        if not result:
            await self.feedback('info', "Failed to save disk image")
        return result


    async def run(self, reset):
        leases = Leases(self.message_bus)
        await self.feedback('authorization', 'checking for a valid lease')
        valid = await leases.booked_now_by_me()
        if not valid:
            await self.feedback('authorization',
                                "Access refused : you have no lease"
                                " on the testbed at this time")
            return False
        await (self.stage1()
               if reset
               else self.feedback('info', "Skipping stage1"))
        return await self.stage2(reset)


    def mark_image_as_partial(self):
        # never mind if that fails, we might call this before
        # the file is created
        try:
            os.rename(self.image, self.image + ".partial")
        except Exception:                               # pylint: disable=w0703
            pass


    def cleanup(self):
        if self.collector:
            self.collector.stop_nowait()
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
                    f"rhubarbe-save failed: {scheduler.why()}")
                return 1
            return 0 if mainjob.result() else 1
        except KeyboardInterrupt:
            self.display.set_goodbye("rhubarbe-save : keyboard interrupt, bye")
            return 1
        finally:
            self.cleanup()
