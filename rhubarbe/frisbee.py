"""
Parsing the output of the remote frisbee app
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w0201,r1705,w1201,w1202

import re

import telnetlib3

from rhubarbe.logger import logger
from rhubarbe.telnet import TelnetProxy
from rhubarbe.config import Config


# NOTE: this code is based on telnetlib3 0.5.0
# in 1.0 there is no telnetlib3.TerminalShell
class FrisbeeParser(telnetlib3.TerminalShell):
    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.bytes_line = b""
        self.total_chunks = 0

    def feed_byte(self, incoming):                      # pylint: disable=w0221
        if incoming == b"\n":
            self.parse_line()
            self.bytes_line = b""
        else:
            self.bytes_line += incoming

    def ip(self):                                       # pylint: disable=c0103
        return self.client.proxy.control_ip

    def feedback(self, field, msg):
        self.client.proxy.message_bus.put_nowait({'ip': self.ip(), field: msg})

    def send_percent(self, percent):
        self.feedback('percent', percent)

    # parse frisbee output
    # tentatively ported from nitos_testbed ruby code but never tested
    matcher_new_style_progress = \
        re.compile(r'[\.sz]{60,75}\s+\d+\s+(?P<remaining_chunks>\d+)')
    matcher_total_chunks = \
        re.compile(r'.*team after (?P<time>[0-9\.]*).*'
                   r'File is (?P<total_chunks>[0-9]+) chunks.*')
    matcher_old_style_progress = \
        re.compile(r'^Progress:\s+(?P<percent>[\d]+)%.*')
    matcher_final_report = \
        re.compile(r'^Wrote\s+(?P<total>\d+)\s+bytes \((?P<actual>\d+).*')
    matcher_short_write = \
        re.compile(r'.*Short write.*')
    matcher_status = \
        re.compile(r'FRISBEE-STATUS=(?P<status>\d+)')

    def parse_line(self):
        line = self.bytes_line.decode().strip()
        logger.debug("line from frisbee:" + line)
        #
        match = self.matcher_new_style_progress.match(line)
        if match:
            if self.total_chunks == 0:
                logger.error("ip={}: new frisbee: cannot report progress, "
                             "missing total chunks"
                             .format(self.ip()))
                return
            percent = int(100 * (1 - int(match.group('remaining_chunks'))
                                 / self.total_chunks))
            self.send_percent(percent)
        #
        match = self.matcher_total_chunks.match(line)
        if match:
            self.total_chunks = int(match.group('total_chunks'))
            self.send_percent(0)
            return
        #
        match = self.matcher_old_style_progress.match(line)
        if match:
            self.send_percent(match.group('percent'))
            return
        #
        match = self.matcher_final_report.match(line)
        if match:
            logger.info("ip={ip} FRISBEE END: "
                        "total = {total} bytes, "
                        "actual = {actual} bytes"
                        .format(ip=self.ip(), total=match.group('total'),
                                actual=match.group('actual')))
            self.send_percent(100)
            return
        #
        match = self.matcher_short_write.match(line)
        if match:
            self.feedback('frisbee_error',
                          "Something went wrong with frisbee (short write...)")
            return
        #
        match = self.matcher_status.match(line)
        if match:
            status = int(match.group('status'))
            self.feedback('frisbee_retcod', status)


class Frisbee(TelnetProxy):
    async def connect(self):
        await self._try_to_connect(shell=FrisbeeParser)

    async def wait(self):
        await self._wait_until_connect(shell=FrisbeeParser)

    async def run(self, multicast_ip, port):
        control_ip = self.control_ip
        the_config = Config()
        client = the_config.value('frisbee', 'client')
        hdd = the_config.value('frisbee', 'hard_drive')
        self.command = (
            "{client} -i {control_ip} -m {multicast_ip} -p {port} {hdd}"
            .format(client=client, control_ip=control_ip,
                    multicast_ip=multicast_ip, port=port, hdd=hdd))

        logger.info("on {} : running command {}"
                    .format(self.control_ip, self.command))
        await self.feedback('frisbee_status', "starting frisbee client")

        eof = chr(4)
        eol = '\n'
        # print out exit status so the parser can catch it and expose it
        command = self.command
        command = command + "; echo FRISBEE-STATUS=$?"
        # make sure the command is sent (EOL)
        # and that the session terminates afterwards (exit + EOF)
        command = command + "; exit" + eol + eof
        self._protocol.stream.write(self._protocol.shell.encode(command))

        # wait for telnet to terminate
        await self._protocol.waiter_closed
        return True
