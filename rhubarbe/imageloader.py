import asyncio

import util
from frisbeed import Frisbeed

from config import the_config
from leases import Leases

class ImageLoader:

    def __init__(self, nodes,  image, bandwidth,
                 message_bus, monitor):
        self.nodes = nodes
        self.image = image
        self.bandwidth = bandwidth
        self.monitor = monitor
        self.message_bus = message_bus
        #
        self.frisbeed = None

    @asyncio.coroutine
    def feedback(self, field, msg):
        yield from self.message_bus.put({field: msg})

    @asyncio.coroutine
    def stage1(self):
        idle = int(the_config.value('nodes', 'idle_after_reset'))
        yield from asyncio.gather(*[node.load_stage1(idle) for node in self.nodes])

    @asyncio.coroutine
    def start_frisbeed(self):
        self.frisbeed = Frisbeed(self.image, self.bandwidth, self.message_bus)
        ip_port = yield from self.frisbeed.start()
        return ip_port

    @asyncio.coroutine
    def stage2(self, reset):
        """
        wait for all nodes to be telnet-friendly
        then run frisbee in all of them
        and reset the nodes afterwards, unless told otherwise
        """
        # start_frisbeed will return the ip+port to use 
        ip, port = yield from self.start_frisbeed()
        yield from asyncio.gather(*[node.load_stage2(ip, port, reset) for node in self.nodes])
        # we can now kill the server
        yield from self.frisbeed.stop()

    # this is synchroneous
    def nextboot_cleanup(self):
        """
        Remove nextboot symlinks for all nodes in this selection
        so next boot will be off the harddrive
        """
        [node.manage_nextboot_symlink('harddrive') for node in self.nodes]

    @asyncio.coroutine
    def run(self, reset):
        leases = Leases(self.message_bus)
        self.feedback('authentication','checking for a valid lease')
        valid = yield from leases.is_valid()
        if not valid:
            yield from self.feedback('authentication','access denied')
            yield from self.feedback("Access refused : you have no lease on the testbed at this time")
        else:
            yield from self.feedback('authentication','access granted')
            yield from (self.stage1() if reset else self.feedback({'info': "Skipping stage1"}))
            yield from (self.stage2(reset))
        yield from self.monitor.stop()

    # from http://stackoverflow.com/questions/30765606/whats-the-correct-way-to-clean-up-after-an-interrupted-event-loop
    def main(self, reset, timeout):
        loop = asyncio.get_event_loop()
        t1 = util.self_manage(self.run(reset))
        t2 = util.self_manage(self.monitor.run())
        tasks = asyncio.gather(t1, t2)
        wrapper = asyncio.wait_for(tasks, timeout)
        try:
            loop.run_until_complete(wrapper)
            return 0
        except KeyboardInterrupt as e:
            self.monitor.set_goodbye("rhubarbe-load : keyboard interrupt - exiting")
            tasks.cancel()
            loop.run_forever()
            tasks.exception()
            return 1
        except asyncio.TimeoutError as e:
            self.monitor.set_goodbye("rhubarbe-load : timeout expired after {}s".format(self.timeout))
            return 1
        finally:
            self.frisbeed and self.frisbeed.stop_nowait()
            self.nextboot_cleanup()
            self.monitor.stop_nowait()
            loop.close()
