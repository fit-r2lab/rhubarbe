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
        # set when a specific error message has been issued
        self.explained = False

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
    matcher_no_disk = \
        re.compile(r'^RHUBARBE-NO-DISK: (?P<message>.*)')
    matcher_disk_too_small = \
        re.compile(r'^RHUBARBE-DISK-TOO-SMALL: (?P<hdd>\S+) has '
                   r'(?P<has>\d+) sectors, the image needs (?P<needs>\d+)')

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
        match = self.matcher_no_disk.match(line)
        if match:
            self.explained = True
            self.feedback(
                'frisbee_error',
                f"{match.group('message')} - the frisbee kernel has no "
                f"AHCI support: check that the BIOS is configured for "
                f"IDE (not AHCI/SATA) mode")
            return
        #
        match = self.matcher_disk_too_small.match(line)
        if match:
            self.explained = True
            has, needs = int(match.group('has')), int(match.group('needs'))
            # sectors are 512 bytes; show decimal GB, like disk vendors
            self.feedback(
                'frisbee_error',
                f"disk too small: {match.group('hdd')} has "
                f"{has * 512 / 1e9:.1f} GB ({has} sectors), "
                f"the image needs {needs * 512 / 1e9:.1f} GB "
                f"({needs} sectors)")
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

    async def run(self, multicast_ip, port, min_sectors=None):
        """
        if min_sectors is set, the frisbee client is only started
        if the node's disk has at least that many 512-byte sectors
        """
        control_ip = self.control_ip
        the_config = Config()
        client = the_config.value('frisbee', 'client')
        hdd = the_config.value('frisbee', 'hard_drive')
        self.command = (
            f"{client} -i {control_ip} -m {multicast_ip} -p {port} {hdd}")
        # sectors count as seen by the node's kernel
        size_file = f"/sys/block/{hdd.split('/')[-1]}/size"
        size_check = ""
        if min_sectors:
            size_check = (
                f"elif [ $(cat {size_file}) -lt {min_sectors} ]; then "
                f"echo RHUBARBE-DISK-TOO-SMALL: {hdd} has "
                f"$(cat {size_file}) sectors, "
                f"the image needs {min_sectors}; false; ")
        self.command = (
            f"if [ ! -e {size_file} ]; then "
            f"echo RHUBARBE-NO-DISK: {hdd} not found, "
            f"block devices are: $(ls /sys/block | tr '\\n' ' '); false; "
            f"{size_check}"
            f"else {self.command}; fi")

        logger.info(f"on {self.control_ip} : running command {self.command}")
        await self.feedback('frisbee_status', "starting frisbee client")

        retcod = await self.session([self.command])

        logger.info(f"frisbee on {self.control_ip} returned {retcod}")
        if not retcod and not self.parser.explained:
            tail = " | ".join(l.strip() for l in self.last_lines[-3:]
                              if l.strip())
            await self.feedback('frisbee_error',
                                f"frisbee failed: {tail or 'no output'}")

        return retcod
