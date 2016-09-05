import os
import asyncio

from rhubarbe.collector import Collector
from rhubarbe.leases import Leases
from rhubarbe.config import Config
import rhubarbe.util as util

class ImageSaver:
    def __init__(self, node, image, radical, message_bus, display, comment):
        self.node = node
        self.image = image
        self.radical = radical
        self.message_bus = message_bus
        self.display = display
        self.comment = comment
        #
        self.collector = None

    async def feedback(self, field, msg):
        yield from self.message_bus.put({field: msg})

    # this is exactly as imageloader
    async def stage1(self):
        the_config = Config()
        idle = int(the_config.value('nodes', 'idle_after_reset'))
        yield from self.node.reboot_on_frisbee(idle)

    # this is synchroneous
    def nextboot_cleanup(self):
        """
        Remove nextboot symlinks for all nodes in this selection
        so next boot will be off the harddrive
        """
        self.node.manage_nextboot_symlink('harddrive')

    async def start_collector(self):
        self.collector = Collector(self.image, self.message_bus)
        port = yield from self.collector.start()
        return port

    async def stage2(self, reset):
        """
        run collector (a netcat server)
        then wait for the node to be telnet-friendly,
        then run imagezip on the node
        reset node when finished unless reset is False
        """
        # start_frisbeed will return the ip+port to use 
        yield from self.feedback('info', "Saving image from {}".format(self.node))
        port = yield from self.start_collector()
        yield from self.node.run_imagezip(port, reset, self.radical, self.comment)
        # we can now kill the server
        self.collector.stop_nowait()

    async def run(self, reset):
        leases = Leases(self.message_bus)
        yield from self.feedback('authorization','checking for a valid lease')
        valid = yield from leases.currently_valid()
        if not valid:
            yield from self.feedback('authorization',
                                     "Access refused : you have no lease on the testbed at this time")
        else:
            yield from (self.stage1() if reset else self.feedback('info', "Skipping stage1"))
            yield from (self.stage2(reset))
        yield from self.display.stop()

    def mark_image_as_partial(self):
        # never mind if that fails, we might call this before
        # the file is created
        try:
            os.rename(self.image, self.image + ".partial")
        except:
            pass

    def main(self, reset, timeout):
        loop = asyncio.get_event_loop()
        t1 = util.self_manage(self.run(reset))
        t2 = util.self_manage(self.display.run())
        tasks = asyncio.gather(t1, t2)
        wrapper = asyncio.wait_for(tasks, timeout)
        try:
            loop.run_until_complete(wrapper)
            return 0
        except KeyboardInterrupt as e:
            self.mark_image_as_partial()
            self.display.set_goodbye("rhubarbe-save : keyboard interrupt - exiting")
            tasks.cancel()
            loop.run_forever()
            tasks.exception()
            return 1
        except asyncio.TimeoutError as e:
            self.mark_image_as_partial()
            self.display.set_goodbye("rhubarbe-save : timeout expired after {}s"
                                     .format(timeout))
            return 1
        finally:
            self.nextboot_cleanup()
            self.collector and self.collector.stop_nowait()
            self.display.epilogue()
            loop.close()
