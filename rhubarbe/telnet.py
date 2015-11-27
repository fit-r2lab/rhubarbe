import time
import random
from logger import logger

import asyncio
import telnetlib3


"""
TelnetProxy is what controls the telnet connection to the node,
it is subclassed as Frisbee and ImageZip

As per the design of telnetlib3, it uses a couple helper classes
* the shell (telnetlib3.TerminalShell) is what receives the session's output,
  so we have for example a FrisbeeParser class that acts as such a shell
* a client (telnetlib3.TelnetClient); 
  that we specialize so we can propagate our own stuff
  (the telnetproxy instance primarily) down to FrisbeeParser

This class essentially is subclassed as classes Frisbee and ImageZip  
"""

# one painful trick here is, we need to pass the shell class when connecting, even though
# in our usage model it would be more convenient to define this when the command is run

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
        from config import the_config
        self.port = int(the_config.value('networking', 'telnet_port'))
        self.backoff = float(the_config.value('networking', 'telnet_backoff'))
        # internals
        self._transport = None
        self._protocol = None

    def is_ready(self):
        # xxx for now we don't check that frisbee is installed and the expected version
        return self._protocol is not None

    @asyncio.coroutine
    def feedback(self, field, msg):
        yield from self.message_bus.put({'ip': self.control_ip, field: msg})

    @asyncio.coroutine
    def _try_to_connect(self, shell=telnetlib3.TerminalShell):
        
        # a little closure to capture our ip and expose it to the parser
        def client_factory():
            return TelnetClient(proxy = self, encoding='utf-8', shell=shell)

        yield from self.feedback('frisbee_status', "trying to telnet..")
        logger.info("Trying to telnet to {}".format(self.control_ip))
        loop = asyncio.get_event_loop()
        try:
            self._transport, self._protocol = \
              yield from loop.create_connection(client_factory, self.control_ip, self.port)
            logger.info("{}: telnet connected".format(self.control_ip))
            return True
        except Exception as e:
#            import traceback
#            traceback.print_exc()
            # just making sure
            self._transport, self._protocol = None, None

    @asyncio.coroutine
    def _wait_until_connect(self, shell=telnetlib3.TerminalShell):
        """
        wait for the telnet server to come up
        this has no native timeout mechanism
        """
        while True:
             yield from self._try_to_connect(shell)
             if self.is_ready():
                 return True
             else:
                 backoff = self.backoff*(0.5 + random.random())
                 yield from self.feedback(
                     'frisbee_status',
                     "backing off for {:.3}s".format(backoff))
                 yield from asyncio.sleep(backoff)
