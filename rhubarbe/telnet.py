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

MAX_BUF = 16 * 1024


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
    * invoke frisbee when rload'ing
    * invoke imagezip when rsave'ing
    """

    def __init__(self, control_ip, message_bus):
        self.control_ip = control_ip
        self.message_bus = message_bus
        # config
        the_config = Config()
        self.port = int(the_config.value('networking', 'telnet_port'))
        self.backoff = float(the_config.value('networking', 'telnet_backoff'))
        self.connect_timeout = float(the_config.value('networking', 'telnet_timeout'))
        self.connect_minwait = float(the_config.value('networking', 'telnet_connect_minwait'))
        self.connect_maxwait = float(the_config.value('networking', 'telnet_connect_maxwait'))
        # internals
        self.running = False
        self._reader = None
        self._writer = None


    def is_ready(self):
        return self._writer is not None


    async def feedback(self, field, msg):
        await self.message_bus.put({'ip': self.control_ip, field: msg})


    async def try_to_connect(self):

        # a little closure to capture our ip and expose it to the parser
        def client_factory():
            return TelnetClient(proxy=self, encoding='utf-8')

        await self.feedback('frisbee_status', "trying to telnet..")
        logger.info(f"Trying to telnet on {self.control_ip}")
        try:
            self._reader, self._writer = await asyncio.wait_for(
                telnetlib3.open_connection(
                    self.control_ip, 23, shell=None,
                    connect_minwait=self.connect_minwait,
                    connect_maxwait=self.connect_maxwait),
                timeout = self.connect_timeout)
        except (asyncio.TimeoutError, OSError) as exc:
            self._reader, self._writer = None, None
        except Exception as exc:
            logger.exception(f"telnet connect: unexpected exception {exc}")


    async def wait_until_connect(self):
        """
        wait for the telnet server to come up
        this has no native timeout mechanism
        """
        while True:
            await self.try_to_connect()
            if self.is_ready():
                return True
            else:
                backoff = self.backoff*(0.5 + random.random())
                await self.feedback('frisbee_status',
                                    f"backing off for {backoff:.3}s")
                await asyncio.sleep(backoff)


    def line_callback(self, line):
        """
        this is intended to be redefined by daughter classes
        it will be called with each piece of data that comes back
        as a result of invoking session()
        no line-asembling is done in the present class for now,
        returned input is triggered as it comes
        """
        logger.error(f"redefine telnet.line_callback()")


    async def session(self, commands):
        """
        given a list of shell commands, will issue them
        before exiting
        all the commands are fired at once in sequence

        return is a boolean that says whether the last command ran OK
        i.e. return is True if retcod is 0, False otherwise
        """

        commands.append('echo _TELNET_STATUS=$?')
        commands.append('exit')

        def parse_status(line):
            os_status = int(line.strip().replace("_TELNET_STATUS=", ""))
            return os_status == 0

        for command in commands:
            logger.debug(f"telnet -> {command}")
            # print(f'[[{command}]]')
            self._writer.write(command + '\n')


        self.running = True
        retcod = False

        line = ""
        while True:
            if self._reader.at_eof():
                break
            recv = await self._reader.read(MAX_BUF)
            for incoming in recv:
                if incoming == "\n":
                    logger.debug(f"telnet <- {line}")
                    if line.startswith("_TELNET_STATUS"):
                        retcod = parse_status(line)
                    self.line_callback(line)
                    line = ""
                else:
                    line += incoming

        self.running = False

        return retcod
