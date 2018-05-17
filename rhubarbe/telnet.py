"""
TelnetProxy is what controls the telnet connection to the node,
it is subclassed as Frisbee and ImageZip

As per the design of telnetlib3, it uses a couple helper classes
* the shell (telnetlib3.TerminalShell) is what receives the session's output,
  so we have for example a FrisbeeParser class that acts as such a shell
* a client (telnetlib3.TelnetClient);
  that we specialize so we can propagate our own stuff
  (the telnetproxy instance primarily) down to FrisbeeParser

This class essentially is the mother class for Frisbee and ImageZip
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0703, w1202

import random
import asyncio
import telnetlib3

from rhubarbe.logger import logger
from rhubarbe.config import Config

# one painful trick here is, we need to pass the shell class when connecting,
# even though in our usage model it would be more convenient
# to define this when the command is run


class TelnetClient(telnetlib3.TelnetClient):
    """
    this specialization of TelnetClient is meant for FrisbeeParser
    to retrieve its correponding TelnetProxy instance
    """
    def __init__(self, proxy, *args, **kwds):
        self.proxy = proxy
        super().__init__(*args, **kwds)


class TelnetProxy:
    """
    a convenience class that help us
    * wait for the telnet server to come up
    * invoke frisbee
    """
    def __init__(self, control_ip, message_bus):
        self.control_ip = control_ip
        self.message_bus = message_bus
        the_config = Config()
        self.port = int(the_config.value('networking', 'telnet_port'))
        self.backoff = float(the_config.value('networking', 'telnet_backoff'))
        self.timeout = float(the_config.value('networking', 'telnet_timeout'))
        # internals
        self._transport = None
        self._protocol = None

    def is_ready(self):
        # xxx for now we don't check that frisbee is installed
        # and the expected version
        return self._protocol is not None

    async def feedback(self, field, msg):
        await self.message_bus.put({'ip': self.control_ip, field: msg})

    async def _try_to_connect(self, shell=telnetlib3.TerminalShell):

        # a little closure to capture our ip and expose it to the parser
        def client_factory():
            return TelnetClient(proxy=self, encoding='utf-8', shell=shell)

        await self.feedback('frisbee_status', "trying to telnet..")
        logger.info("Trying to telnet to {}".format(self.control_ip))
        loop = asyncio.get_event_loop()
        try:
            self._transport, self._protocol = \
              await asyncio.wait_for(
                  loop.create_connection(client_factory,
                                         self.control_ip, self.port),
                  self.timeout)
            logger.info("{}: telnet connected".format(self.control_ip))
            return True
        except asyncio.TimeoutError:
            await self.feedback('frisbee_status', "timed out..")
            self._transport, self._protocol = None, None
        except Exception:
            logger.exception("telnet connect: unexpected exception {}")
            self._transport, self._protocol = None, None

    async def _wait_until_connect(self, shell=telnetlib3.TerminalShell):
        """
        wait for the telnet server to come up
        this has no native timeout mechanism
        """
        while True:
            await self._try_to_connect(shell)
            if self.is_ready():
                return True
            else:
                backoff = self.backoff*(0.5 + random.random())
                await self.feedback(
                    'frisbee_status',
                    "backing off for {:.3}s".format(backoff))
                await asyncio.sleep(backoff)
