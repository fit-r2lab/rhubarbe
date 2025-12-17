# trap signals for a more condensed output
import signal
import functools

import asyncio

from rhubarbe.logger import monitor_logger as logger


class MonitorLoop:

    def __init__(self, message):
        self.message = message

    def run(self, async_main):

        async def async_main_wrapper():
            loop = asyncio.get_running_loop()

            def exiting(signame):
                logger.info(f"rhubarbe-{self.message}: ¬ received signal {signame} - exiting")
                loop.stop()
                exit(1)

            for signame in ('SIGHUP', 'SIGQUIT', 'SIGINT', 'SIGTERM'):
                loop.add_signal_handler(getattr(signal, signame),
                                        functools.partial(exiting, signame))

            try:
                await async_main
                return 0
            except KeyboardInterrupt:
                logger.info(f"rhubarbe-{self.message} : keyboard interrupt - exiting")
                return 1
            except asyncio.TimeoutError:
                logger.info(f"rhubarbe-{self.message} : asyncio timeout expired")
                return 1

        with asyncio.Runner() as runner:
            return runner.run(async_main_wrapper())
