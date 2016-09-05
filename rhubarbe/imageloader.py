import asyncio

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
        yield from self.message_bus.put({field: msg})

    async def stage1(self):
        the_config = Config()
        idle = int(the_config.value('nodes', 'idle_after_reset'))
        yield from asyncio.gather(*[node.reboot_on_frisbee(idle) for node in self.nodes])

    async def start_frisbeed(self):
        self.frisbeed = Frisbeed(self.image, self.bandwidth, self.message_bus)
        ip_port = yield from self.frisbeed.start()
        return ip_port

    async def stage2(self, reset):
        """
        wait for all nodes to be telnet-friendly
        then run frisbee in all of them
        and reset the nodes afterwards, unless told otherwise
        """
        # start_frisbeed will return the ip+port to use 
        ip, port = yield from self.start_frisbeed()
        yield from asyncio.gather(*[node.run_frisbee(ip, port, reset) for node in self.nodes])
        # we can now kill the server
        self.frisbeed.stop_nowait()

    # this is synchroneous
    def nextboot_cleanup(self):
        """
        Remove nextboot symlinks for all nodes in this selection
        so next boot will be off the harddrive
        """
        [node.manage_nextboot_symlink('harddrive') for node in self.nodes]

    async def run(self, reset):
        leases = Leases(self.message_bus)
        yield from self.feedback('authorization','checking for a valid lease')
        valid = yield from leases.currently_valid()
        if not valid:
            yield from self.feedback('authorization',
                                     "Access refused : you have no lease on the testbed at this time")
        else:
            yield from self.feedback('authorization','access granted')
            yield from (self.stage1() if reset else self.feedback('info', "Skipping stage1"))
            yield from (self.stage2(reset))
        yield from self.display.stop()

    # from http://stackoverflow.com/questions/30765606/whats-the-correct-way-to-clean-up-after-an-interrupted-event-loop
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
            self.display.set_goodbye("rhubarbe-load : keyboard interrupt - exiting")
            tasks.cancel()
            loop.run_forever()
            tasks.exception()
            return 1
        except asyncio.TimeoutError as e:
            self.display.set_goodbye("rhubarbe-load : timeout expired after {}s".format(timeout))
            return 1
        finally:
            self.frisbeed and self.frisbeed.stop_nowait()
            self.nextboot_cleanup()
            self.display.epilogue()
            loop.close()
