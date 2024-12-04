# trap signals for a more condensed output
import signal
import functools

import asyncio

from rhubarbe.logger import monitor_logger as logger


class MonitorLoop:

    def __init__(self, message):
        self.message = message

    def run(self, async_main):

        # using new_event_loop() instead here results in a runtime
        # error with apparently 2 separate event loops active
        loop = asyncio.get_event_loop()

        def exiting(signame):
            logger.info(f"rhubarbe-{self.message}: ¬ received signal {signame} - exiting")
            loop.stop()
            exit(1)

        for signame in ('SIGHUP', 'SIGQUIT', 'SIGINT', 'SIGTERM'):
            loop.add_signal_handler(getattr(signal, signame),
                                    functools.partial(exiting, signame))

        try:
            task = asyncio.ensure_future(async_main)
            loop.run_until_complete(task)
            return 0
        except KeyboardInterrupt:
            logger.info(f"rhubarbe-{self.message} : keyboard interrupt - exiting")
            task.cancel()
            loop.run_forever()
            task.exception()
            return 1
        except asyncio.TimeoutError:
            logger.info(f"rhubarbe-{self.message} : asyncio timeout expired")
            return 1
        finally:
            loop.close()
