"""
Parsing the output of a remote imagezip process
for animating rhubarbe save
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, c0103, w1201, w1202, w1203

import os
import time
import asyncio

from rhubarbe.logger import logger
from rhubarbe.config import Config
from rhubarbe.telnet import TelnetProxy


class ImageZip(TelnetProxy):

    async def ticker(self):
        await asyncio.sleep(0.5)
        while self.running:
            await self.feedback('tick', '')
            await asyncio.sleep(0.15)
        await self.feedback('tick', 'END')

    # we don't parse anything here because there does not seem to be a way
    # to estimate some total first off, and later get percentages
    # useful to see the logs:
    # self.feedback('imagezip_raw', line)
    # send 10 ticks in a raw - not a good idea
    # for i in range(10): self.feedback('tick', '')
    def line_callback(self, line):
        pass


    async def run(self, port, nodename,                 # pylint: disable=r0914
                  radical, comment):
        the_config = Config()
        server_ip = the_config.local_control_ip()
        imagezip = the_config.value('frisbee', 'imagezip')
        netcat = the_config.value('frisbee', 'netcat')
        # typically /dev/sda
        hdd = the_config.value('frisbee', 'hard_drive')
        # typically /dev/sda1
        root_partition = the_config.value('frisbee', 'root_partition')
        commands = []
        if root_partition and root_partition.lower() != 'none':
            command = ""
            # Managing the /etc/rhubarbe-image stamp
            # typically /mnt
            mount_point = the_config.value('frisbee', 'mount_point')
            date = time.strftime("%Y-%m-%d@%H:%M", time.localtime())
            who = os.getlogin()
            # create mount point if needed
            command += f'[ -d {mount_point} ] || mkdir {mount_point}; '
            # mount it, and only if successful ...
            command += f'mount {root_partition} {mount_point} && '
            # add to the stamp, and umount
            # beware of {{ and }} as these are formats
            command += (f'{{ echo "{date} - node {nodename} '
                        f' - image {radical} - by {who}"')
            if comment:
                command += f'" - {comment}"'
            command += f' >> {mount_point}/etc/rhubarbe-image ; '
            command += f'umount {mount_point}; }} ; '
            commands.append(command)

        # need to set pipefail so that we capture an error if ANY
        # of the two commands in the pipe fail
        commands.append("set -o pipefail")
        command = f"{imagezip} -o -z1 {hdd} - | {netcat} {server_ip} {port}"
        commands.append(command)
        logger.info(f"on {self.control_ip} : running command {command}")

        await self.feedback('frisbee_status',
                            f"starting imagezip on {self.control_ip}")

        # print out exit status so the parser can catch it and expose it
        retcod, _ = await asyncio.gather(
            self.session(commands),
            self.ticker(),
        )
        logger.info(f"imagezip on {self.control_ip} returned {retcod}")

        return retcod
