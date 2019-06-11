"""
Controller for the frisbee daemon that sends images when doing
rhubarbe load
"""

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0201, r1705, w1201, w1202, w1203

from pathlib import Path

import asyncio

from rhubarbe.logger import logger
from rhubarbe.config import Config


class Frisbeed:
    """
    Controller for a frisbeed instance
    """
    def __init__(self, image, bandwidth, message_bus):
        self.image = str(image)
        self.bandwidth = bandwidth
        self.message_bus = message_bus
        #
        self.multicast_group = None
        self.multicast_port = None
        self.subprocess = None

    def __repr__(self):
        text = "<frisbeed"
        if self.multicast_group:
            text += f"@{self.multicast_group}:{self.multicast_port}"
        text += f" on {Path(self.image).name} at {self.bandwidth} Mibps"
        text += ">"
        return text

    async def feedback(self, field, msg):
        await self.message_bus.put({field: msg})

    def feedback_nowait(self, field, msg):
        self.message_bus.put_nowait({field: msg})

    async def start(self):                              # pylint: disable=r0914
        """
        Start a frisbeed instance
        returns a tuple multicast_group, port_number
        """
        the_config = Config()
        server = the_config.value('frisbee', 'server')
        server_options = the_config.value('frisbee', 'server_options')
        local_ip = the_config.local_control_ip()
        # in Mibps
        bandwidth = self.bandwidth * 2**20
        # should use default.ndz if not provided
        command_common = [
            server, "-i", local_ip, "-W", str(bandwidth), self.image
            ]
        # add configured extra options
        command_common += server_options.split()

        nb_attempts = int(the_config.value('networking', 'pattern_size'))
        pat_ip = the_config.value('networking', 'pattern_multicast')
        pat_port = the_config.value('networking', 'pattern_port')
        for i in range(1, nb_attempts+1):
            pat = str(i)
            multicast_group = pat_ip.replace('*', pat)
            multicast_port = str(eval(                  # pylint: disable=w0123
                pat_port.replace('*', pat)))
            command = command_common + [
                "-m", multicast_group, "-p", multicast_port,
                ]
            self.subprocess = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
                )
            await asyncio.sleep(1)
            # after such a short time, frisbeed should not have returned yet
            # if it has, we try our luck on another couple (ip, port)
            command_line = " ".join(command)
            if self.subprocess.returncode is None:
                self.multicast_group = multicast_group
                self.multicast_port = multicast_port
                await self.feedback('info', f"started {self}")
                return multicast_group, multicast_port
            else:
                logger.warning(f"failed to start frisbeed with `{command_line}`"
                               f" -> {self.subprocess.returncode}")
        logger.critical(f"could not start frisbee server !!! on {self.image}")
        raise Exception(f"could not start frisbee server !!! on {self.image}")

    def stop_nowait(self):
        # make it idempotent
        if self.subprocess:
            self.subprocess.kill()
            self.subprocess = None
            self.feedback_nowait('info', f"stopped {self}")
