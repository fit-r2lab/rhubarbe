import asyncio

from rhubarbe.logger import logger
from rhubarbe.config import Config

class Frisbeed:
    def __init__(self, image, bandwidth, message_bus):
        self.subprocess = None
        self.image = image
        self.bandwidth = bandwidth
        self.message_bus = message_bus
    
    async def feedback(self, field, msg):
        await self.message_bus.put({field: msg})

    def feedback_nowait(self, field, msg):
        self.message_bus.put_nowait({field: msg})

    async def start(self):
        """
        Start a frisbeed instance
        returns a tuple multicast_address, port_number
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
        pat_ip   = the_config.value('networking', 'pattern_multicast')
        pat_port = the_config.value('networking', 'pattern_port')
        for i in range(1, nb_attempts+1):
            pat = str(i)
            multicast_group = pat_ip.replace('*', pat)
            multicast_port = str(eval(pat_port.replace('*', pat)))
            command = command_common + [
                "-m", multicast_group, "-p", multicast_port,
                ]
            self.subprocess = await asyncio.create_subprocess_exec(
                *command,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.STDOUT
                )
            await asyncio.sleep(1)
            # after such a short time, frisbeed should not have returned yet
            # if is has, we try our luck on another couple (ip, port)
            command_line = " ".join(command)
            if self.subprocess.returncode is None:
                logger.info("frisbeed started: {}".format(command_line))
                await self.feedback('info', "frisbee server: image {} - bw = {} bps"
                                    .format(os.path.basename(self.image), bandwidth))
                self.multicast_group = multicast_group
                self.multicast_port = multicast_port
                return multicast_group, multicast_port
            else:
                logger.warning("failed to start frisbeed with {}".format(command_line))
        logger.critical("Could not find a free IP multicast address + port to start frisbeed")
        raise Exception("Could not start frisbee server")


#    async def stop(self):
#        self.stop_nowait()
        
    def stop_nowait(self):
        # make it idempotent
        if self.subprocess:
            self.subprocess.kill()
            self.subprocess = None
            logger.info("frisbeed ({}:{}) stopped".format(self.multicast_group, self.multicast_port))
            self.feedback_nowait('info', "frisbee server ({}:{}) stopped".format(self.multicast_group, self.multicast_port))
