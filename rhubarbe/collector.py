"""
The collector is used when saving an images
it starts a local netcat process that binds a specific port
and stores everything on the newly saved image file
"""

import asyncio

from rhubarbe.logger import logger
from rhubarbe.config import Config

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w1202,r1705


class Collector:
    def __init__(self, image, message_bus):
        self.image = image
        self.message_bus = message_bus
        #
        self.subprocess = None
        self.port = None

    async def feedback(self, field, msg):
        await self.message_bus.put({field: msg})

    def feedback_nowait(self, field, msg):
        self.message_bus.put_nowait({field: msg})

    async def start(self):                              # pylint: disable=r0914
        """
        Start a collector instance; returns a port_number
        """
        the_config = Config()
        netcat = the_config.value('frisbee', 'netcat')
        local_ip = the_config.local_control_ip()

        # should use default.ndz if not provided
        # use shell-style as we rather have bash handle the redirection
        # we instruct bash to exec nc;
        # otherwise when cleaning up we just kill the bash process
        # but nc is left lingering behind
        # WARNING: it is intended that format contains {port}
        # for future formatting
        command_format_ubuntu = (
            f"exec {netcat} -d -l {local_ip} {{port}} > {self.image}"
            f" 2> /dev/null")
        command_format_fedora = (
            f"exec {netcat}    -l {local_ip} {{port}} > {self.image}"
            f" 2> /dev/null")

        netcat_style = the_config.value('frisbee', 'netcat_style')
        if netcat_style not in ('fedora', 'ubuntu'):
            message = f"wrong netcat_style {netcat_style}"
            print(message)
            raise Exception(message)
        command_format = (
            command_format_fedora if netcat_style == 'fedora'
            else command_format_ubuntu)

        nb_attempts = int(the_config.value('networking', 'pattern_size'))
        pat_port = the_config.value('networking', 'pattern_port')
        for i in range(1, nb_attempts+1):
            pat = str(i)
            port = str(eval(                            # pylint: disable=w0123
                pat_port.replace('*', pat)))
            command = command_format.format(port=port)
            self.subprocess = await asyncio.create_subprocess_shell(command)
            await asyncio.sleep(1)
            # after such a short time, frisbeed should not have returned yet
            # if is has, we try our luck on another couple (ip, port)
            command_line = command
            if self.subprocess.returncode is None:
                logger.info(f"collector started: {command_line}")
                await self.feedback(
                    'info', f"collector started on {self.image}")
                self.port = port
                return port
            else:
                logger.warning(
                    f"failed to start collector with {command_line}")
        logger.critical("Could not find a free port to start collector")
        raise Exception("Could not start collector server")

#    async def stop(self):
#        self.stop_nowait()

    def stop_nowait(self):
        # make it idempotent
        if self.subprocess:
            # when everything is running fine, nc will exit on its own
            try:
                self.subprocess.kill()
            except Exception:                           # pylint: disable=w0703
                pass
            self.subprocess = None
            logger.info(f"collector (on port {self.port}) stopped")
            self.feedback_nowait(
                'info', f"image collector server (on port {self.port}) stopped")
