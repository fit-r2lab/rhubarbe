import asyncio

from collector import Collector
from config import the_config
import util

class ImageSaver:
    def __init__(self, node, image, message_bus, monitor):
        self.node = node
        self.image = image
        self.message_bus = message_bus
        self.monitor = monitor
        #
        self.collector = None

    @asyncio.coroutine
    def feedback(self, field, msg):
        yield from self.message_bus.put({field: msg})

    # this is exactly as imageloader
    @asyncio.coroutine
    def stage1(self):
        idle = int(the_config.value('nodes', 'idle_after_reset'))
        yield from self.node.save_stage1(idle)

    @asyncio.coroutine
    def start_collector(self):
        self.collector = Collector(self.image, self.message_bus)
        port = yield from self.collector.start()
        return port

    @asyncio.coroutine
    def stage2(self, reset):
        """
        run collector (a netcat server)
        then wait for the node to be telnet-friendly,
        then run imagezip on the node
        reset node when finished unless reset is False
        """
        # start_frisbeed will return the ip+port to use 
        port = yield from self.start_collector()
        yield from self.node.save_stage2(port, reset)
        # we can now kill the server
        yield from self.collector.stop()

    @asyncio.coroutine
    def run(self, reset):
        yield from (self.stage1() if reset else self.feedback('info', "Skipping stage1"))
        yield from (self.stage2(reset))
        yield from self.monitor.stop()

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
            self.monitor.set_goodbye("rhubarbe-save : keyboard interrupt - exiting")
            tasks.cancel()
            loop.run_forever()
            tasks.exception()
            return 1
        except asyncio.TimeoutError as e:
            self.monitor.set_goodbye("rhubarbe-save : timeout expired after {}s".format(self.timeout))
            return 1
        finally:
            self.collector and self.collector.stop_nowait()
            self.monitor.stop_nowait()
            loop.close()
        
