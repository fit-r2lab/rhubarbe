import re
from logger import logger

import asyncio
import telnetlib3

from telnet import TelnetProxy

class ImageZipParser(telnetlib3.TerminalShell):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.bytes_line = b""
        self.total_chunks = 0

    def feed_byte(self, x):
        if x == b"\n":
            self.parse_line()
            self.bytes_line = b""
        else:
            self.bytes_line += x

    def ip(self):
        return self.client.proxy.control_ip
    def feedback(self, field, msg):
        self.client.proxy.message_bus.put_nowait({'ip': self.ip(), field: msg})
    def send_percent(self, percent):
        self.feedback('progress', percent)

    # parse imagezip output ????
    def parse_line(self):
        line = self.bytes_line.decode().strip()
        #
        # we don't parse anything here because there does not seem to be a way
        # to estimate some total first off, and later get percentages
        # useful to see the logs:
        # self.feedback('imagezip_raw', line)
        # send 10 ticks in a raw - not a good idea
        # for i in range(10): self.feedback('tick', '')
        
class ImageZip(TelnetProxy):
    @asyncio.coroutine
    def connect(self):
        yield from self._try_to_connect(shell=ImageZipParser)

    @asyncio.coroutine
    def wait(self):
        yield from self._wait_until_connect(shell=ImageZipParser)

    @asyncio.coroutine
    def ticker(self):
        while self._running:
            yield from self.feedback('tick', '')
            yield from asyncio.sleep(0.1)

    @asyncio.coroutine
    def wait_protocol_and_stop_ticker(self):
        yield from self._protocol.waiter_closed
        # hack so we can finish the progressbar
        yield from self.feedback('tick', 'END')
        self._running = False

    @asyncio.coroutine
    def run(self, port):
        from config import the_config
        server_ip = the_config.local_control_ip()
        imagezip = the_config.value('frisbee', 'imagezip')
        netcat = the_config.value('frisbee', 'netcat')
        hdd = the_config.value('frisbee', 'hard_drive')
        self.command = \
          "{imagezip} -o -z1 {hdd} - | {netcat} {server_ip} {port}".format(**locals())

        logger.info("on {} : running command {}".format(self.control_ip, self.command))
        yield from self.feedback('frisbee_status', "starting imagezip on {}".format(self.control_ip))
        
        EOF = chr(4)
        EOL = '\n'
        # print out exit status so the parser can catch it and expose it
        command = self.command
        command = command + "; echo FRISBEE-STATUS=$?"
        # make sure the command is sent (EOL) and that the session terminates afterwards (exit + EOF)
        command = command + "; exit" + EOL + EOF
        self._protocol.stream.write(self._protocol.shell.encode(command))

        # wait for telnet to terminate
        self._running = True
        yield from asyncio.gather(self.ticker(), self.wait_protocol_and_stop_ticker())
