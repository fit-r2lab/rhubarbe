import os
import time
import re
import asyncio
import telnetlib3

from rhubarbe.logger import logger
from rhubarbe.config import Config
from rhubarbe.telnet import TelnetProxy

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
        logger.debug("line from imagezip:" + line)
        #
        # we don't parse anything here because there does not seem to be a way
        # to estimate some total first off, and later get percentages
        # useful to see the logs:
        # self.feedback('imagezip_raw', line)
        # send 10 ticks in a raw - not a good idea
        # for i in range(10): self.feedback('tick', '')
        
class ImageZip(TelnetProxy):
    async def connect(self):
        await self._try_to_connect(shell=ImageZipParser)

    async def wait(self):
        await self._wait_until_connect(shell=ImageZipParser)

    async def ticker(self):
        while self._running:
            await self.feedback('tick', '')
            await asyncio.sleep(0.1)

    async def wait_protocol_and_stop_ticker(self):
        await self._protocol.waiter_closed
        # hack so we can finish the progressbar
        await self.feedback('tick', 'END')
        self._running = False

    async def run(self, port, nodename, radical, comment):
        the_config = Config()
        server_ip = the_config.local_control_ip()
        imagezip = the_config.value('frisbee', 'imagezip')
        netcat = the_config.value('frisbee', 'netcat')
        # typically /dev/sda
        hdd = the_config.value('frisbee', 'hard_drive')
        # typically /dev/sda1
        root_partition = the_config.value('frisbee', 'root_partition')
        command = ""
        if root_partition and root_partition.lower() != 'none':
            # Managing the /etc/rhubarbe-image stamp
            # typically /mnt
            mount_point = the_config.value('frisbee', 'mount_point')
            date = time.strftime("%Y-%m-%d@%H:%M", time.localtime())
            who = os.getlogin()
            # create mount point if needed
            format = '[ -d {mount_point} ] || mkdir {mount_point}; '
            # mount it, and only if successful ...
            format += 'mount {root_partition} {mount_point} && '
            # add to the stamp, and umount - beware of {{ and }} as these are formats
            format += '{{ echo "{date} - node {nodename} - image {radical} - by {who}"'
            if comment:
                format += '" - {}"'.format(comment)
            format += ' >> {mount_point}/etc/rhubarbe-image ; umount {mount_point}; }} ; '
            # replace {} 
            command += format.format(**locals())
        command += \
          "{imagezip} -o -z1 {hdd} - | {netcat} {server_ip} {port}".format(**locals())

        logger.info("on {} : running command {}".format(self.control_ip, command))
        await self.feedback('frisbee_status', "starting imagezip on {}".format(self.control_ip))
        
        EOF = chr(4)
        EOL = '\n'
        # print out exit status so the parser can catch it and expose it
        command = command + "; echo FRISBEE-STATUS=$?"
        # make sure the command is sent (EOL) and that the session terminates afterwards (exit + EOF)
        command = command + "; exit" + EOL + EOF
        self._protocol.stream.write(self._protocol.shell.encode(command))

        # wait for telnet to terminate
        self._running = True
        await asyncio.gather(self.ticker(), self.wait_protocol_and_stop_ticker())
        return True
