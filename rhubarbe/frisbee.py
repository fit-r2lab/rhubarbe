import re
import asyncio
import telnetlib3

from rhubarbe.logger import logger
from rhubarbe.telnet import TelnetProxy
from rhubarbe.config import Config

class FrisbeeParser(telnetlib3.TerminalShell):
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
        self.feedback('percent', percent)

    # parse frisbee output
    # tentatively ported from nitos_testbed ruby code but never tested
    matcher_new_style_progress = re.compile('[\.sz]{60,75}\s+\d+\s+(?P<remaining_chunks>\d+)')
    matcher_total_chunks = re.compile('.*team after (?P<time>[0-9\.]*).*File is (?P<total_chunks>[0-9]+) chunks.*')
    matcher_old_style_progress = re.compile('^Progress:\s+(?P<percent>[\d]+)%.*')
    matcher_final_report = re.compile('^Wrote\s+(?P<total>\d+)\s+bytes \((?P<actual>\d+).*')
    matcher_short_write = re.compile('.*Short write.*')
    matcher_status = re.compile('FRISBEE-STATUS=(?P<status>\d+)')

    def parse_line(self):
        line = self.bytes_line.decode().strip()
        logger.debug("line from frisbee:" + line)
        #
        m = self.matcher_new_style_progress.match(line)
        if m:
            if self.total_chunks == 0:
                logger.error("ip={}: new frisbee: cannot report progress, missing total chunks"
                             .format(self.ip()))
                return
            percent = int ( 100 * (1 - int(m.group('remaining_chunks'))/self.total_chunks))
            self.send_percent(percent)
        #
        m = self.matcher_total_chunks.match(line)
        if m:
            self.total_chunks = int(m.group('total_chunks'))
            self.send_percent(0)
            return
        #
        m = self.matcher_old_style_progress.match(line)
        if m:
            self.send_percent(m.group('percent'))
            return
        #
        m = self.matcher_final_report.match(line)
        if m:
            logger.info("ip={ip} FRISBEE END: total = {total} bytes, actual = {actual} bytes"
                  .format(ip=self.ip(), total=m.group('total'), actual=m.group('actual')))
            self.send_percent(100)
            return
        #
        m = self.matcher_short_write.match(line)
        if m:
            self.feedback('frisbee_error', "Something went wrong with frisbee (short write...)...")
            return
        #
        m = self.matcher_status.match(line)
        if m:
            status = int(m.group('status'))
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
        self.command = \
          "{client} -i {control_ip} -m {multicast_ip} -p {port} {hdd}".format(**locals())

        logger.info("on {} : running command {}".format(self.control_ip, self.command))
        await self.feedback('frisbee_status', "starting frisbee client")
        
        EOF = chr(4)
        EOL = '\n'
        # print out exit status so the parser can catch it and expose it
        command = self.command
        command = command + "; echo FRISBEE-STATUS=$?"
        # make sure the command is sent (EOL) and that the session terminates afterwards (exit + EOF)
        command = command + "; exit" + EOL + EOF
        self._protocol.stream.write(self._protocol.shell.encode(command))

        # wait for telnet to terminate
        await self._protocol.waiter_closed
        return True
