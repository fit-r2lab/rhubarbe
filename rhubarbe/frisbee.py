"""
Parsing the output of the remote frisbee app
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0201, r1705, w1201, w1202, w1203

import re

from rhubarbe.logger import logger
from rhubarbe.telnet import TelnetProxy
from rhubarbe.config import Config


class FrisbeeParser:
    def __init__(self, proxy):
        self.proxy = proxy
        self.total_chunks = 0

    def ip(self):
        return self.proxy.control_ip

    def feedback(self, field, msg):
        self.proxy.message_bus.put_nowait({'ip': self.ip(), field: msg})

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

    def parse_line(self, line):
        #
        match = self.matcher_new_style_progress.match(line)
        if match:
            if self.total_chunks == 0:
                logger.error(
                    "ip={self.ip()}: new frisbee: cannot report progress, "
                    "missing total chunks")
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
            logger.info(f"ip={self.ip()} FRISBEE END: "
                        f"total = {match.group('total')} bytes, "
                        f"actual = {match.group('actual')} bytes")
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

    def __init__(self, *args, **kwds):
        super().__init__(*args, **kwds)
        self.parser = FrisbeeParser(self)

    def line_callback(self, line):
        self.parser.parse_line(line)

    async def run(self, multicast_ip, port):
        control_ip = self.control_ip
        the_config = Config()
        client = the_config.value('frisbee', 'client')
        hdd = the_config.value('frisbee', 'hard_drive')
        self.command = (
            f"{client} -i {control_ip} -m {multicast_ip} -p {port} {hdd}")

        logger.info(f"on {self.control_ip} : running command {self.command}")
        await self.feedback('frisbee_status', "starting frisbee client")

        retcod = await self.session([self.command])

        logger.info(f"frisbee on {self.control_ip} returned {retcod}")

        return retcod
