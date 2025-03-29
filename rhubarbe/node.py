"""
The Node object is the handle for doing most CMC-oriented actions like
querying status, turning on or off, and similar
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w0703, w1202

import os.path

import asyncio
import aiohttp

from rhubarbe.logger import logger
from rhubarbe.config import Config
from rhubarbe.inventorynodes import InventoryNodes
from rhubarbe.frisbee import Frisbee
from rhubarbe.imagezip import ImageZip


class Node:                                             # pylint: disable=r0902

    """
    This class allows to talk to various parts of a node
    created from the cmc hostname for convenience
    the inventory lets us spot the other parts (control, essentially)
    """
    def __init__(self, cmc_name, message_bus):
        self.cmc_name = cmc_name
        self.message_bus = message_bus
        self.status = None
        self.action = None
        self.mac = None
        # for monitornodes
        self.id = int("".join([x for x in cmc_name      # pylint: disable=c0103
                               if x in "0123456789"]))

    def __repr__(self):
        return f"<Node {self.control_hostname()}>"

    def is_known(self):
        return self.control_mac_address() is not None

    def control_mac_address(self):
        the_inventory = InventoryNodes()
        return the_inventory.attached_hostname_info(self.cmc_name,
                                                    'control', 'mac')

    def control_ip_address(self):
        the_inventory = InventoryNodes()
        return the_inventory.attached_hostname_info(self.cmc_name,
                                                    'control', 'ip')

    def control_hostname(self):
        the_inventory = InventoryNodes()
        return the_inventory.attached_hostname_info(self.cmc_name,
                                                    'control', 'hostname')

    async def get_status(self):
        """
        returns self.status
        either 'on' or 'off', or None if something wrong is going on
        """
        result = await self._get_cmc_verb('status')
        return result

    async def turn_on(self):
        """
        turn node on; expected result would be 'ok' if it goes fine
        """
        result = await self._get_cmc_verb('on')
        return result

    async def turn_off(self):
        """
        turn node on; expected result would be 'ok' if it goes fine
        """
        result = await self._get_cmc_verb('off')
        return result

    async def do_reset(self):
        """
        turn node on; expected result would be 'ok' if it goes fine
        """
        result = await self._get_cmc_verb('reset')
        return result

    async def get_info(self):
        """
        turn node on; expected result would be 'ok' if it goes fine
        """
        result = await self._get_cmc_verb('info', strip_result=False)
        return result

    async def get_usrpstatus(self):
        """
        returns self.usrpstatus
        either 'on' or 'off', or None if something wrong is going on
        """
        result = await self._get_cmc_verb('usrpstatus')
        return result

    async def turn_usrpon(self):
        """
        turn on node's USRP; expected result would be 'ok' if it goes fine
        """
        result = await self._get_cmc_verb('usrpon')
        return result

    async def turn_usrpoff(self):
        """
        turn off node's USRP; expected result would be 'ok' if it goes fine
        """
        result = await self._get_cmc_verb('usrpoff')
        return result

    async def turn_both_off(self):
        """
        turn off both USRP and node; expected result would be 'ok' if it goes fine
        """
        result1 = await self._get_cmc_verb('usrpoff')
        result2 = await self._get_cmc_verb('off')
        if result1 is None or result2 is None:
            return None
        return f"{result1} - {result2}"

    async def _get_cmc_verb(self, verb, strip_result=True):
        """
        verb typically is 'status', 'on', 'off' or 'info'
        """
        url = f"http://{self.cmc_name}/{verb}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    text = await response.text(encoding='utf-8')
                    if strip_result:
                        text = text.strip()
                    setattr(self, verb, text)
        except aiohttp.client_exceptions.ClientConnectorError:
            logger.info(f"cannot connect to {url}")
            setattr(self, verb, None)
            return None
        except Exception:
            import traceback
            traceback.print_exc()
            setattr(self, verb, None)
            return None
        return getattr(self, verb)

    ####################
    # what status to expect after a message is sent
    expected_map = {
        'on': 'on',
        'reset': 'on',
        'off': 'off'
    }

    async def send_action(self, message="on", check=False, check_delay=1.):
        """
        Actually send action message like 'on', 'off' or 'reset'

        If check is True, waits for check_delay seconds
        before checking again that the status is what is expected, i.e.
        | message  | expected |
        |----------|----------|
        | on,reset | on       |
        | off      | off      |

        return value stored in self.action:

        * if check is false
          * True if request can be sent and returns 'ok',
            or None if something goes wrong
        * otherwise:
          * True to indicate that the node is correctly
            in 'on' mode after checking
          * False to indicate that the node is 'off' after checking
          * None if something goes wrong
        """
        url = f"http://{self.cmc_name}/{message}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    text = await response.text(encoding='utf-8')
        except Exception:
            self.action = None
            return self

        is_ok = text.strip() == 'ok'

        if not check:
            self.action = is_ok
            return self
        await asyncio.sleep(check_delay)
        await self.get_status()
        self.action = self.status == self.expected_map[message]
        return self

    ####################
    message_to_reset_map = {'on': 'reset', 'off': 'on'}

    async def feedback(self, field, message):
        await self.message_bus.put(
            {'ip': self.control_ip_address(), field: message})

    async def ensure_reset(self):
        if self.status is None:
            await self.get_status()
        # still no status: means the CMC does not answer
        if self.status not in self.message_to_reset_map:
            await self.feedback(
                'reboot', f"Cannot get status at {self.cmc_name} (status={self.status})")
            return
        message_to_send = self.message_to_reset_map[self.status]
        await self.feedback(
            'reboot', f"Sending message '{message_to_send}' to CMC {self.cmc_name}")
        await self.send_action(message_to_send, check=True)
        if not self.action:
            await self.feedback(
                'reboot', f"Failed to send message {message_to_send} to CMC {self.cmc_name}")

    # used to be a coroutine, but since we need this
    # when dealing by KeybordInterrupt, it appears much safer
    # to just keep it a traditional function
    def manage_nextboot_symlink(self, action):
        """
        Messes with the symlink in /tftpboot/pxelinux.cfg/
        Depending on 'action'
        * 'cleanup' or 'harddrive' : clear the symlink
          corresponding to this CMC
        * 'frisbee' : define a symlink so that next boot
          will run the frisbee image
        see rhubarbe.conf for configurable options
        """

        the_config = Config()
        root = the_config.value('pxelinux', 'config_dir')
        frisbee = the_config.value('pxelinux', 'frisbee_image')

        # of the form 01-00-03-1d-0e-03-53
        mylink = "01-" + self.control_mac_address().replace(':', '-')
        source = os.path.join(root, mylink)

        if action in ('cleanup', 'harddrive'):
            if os.path.exists(source):
                logger.info(f"Removing {source}")
                os.remove(source)
        elif action in ('frisbee', ):
            if os.path.exists(source):
                os.remove(source)
            logger.info(f"Creating {source}")
            os.symlink(frisbee, source)
        else:
            logger.critical(
                f"manage_nextboot_symlink : unknown action {action}")


    ##########
    async def wait_for_telnet(self, service):
        ipaddr = self.control_ip_address()
        if service == 'frisbee':
            self.frisbee = Frisbee(ipaddr, self.message_bus)
            await self.frisbee.wait_until_connect()
        elif service == 'imagezip':
            self.imagezip = ImageZip(ipaddr, self.message_bus)
            await self.imagezip.wait_until_connect()

    async def reboot_on_frisbee(self, idle):
        self.manage_nextboot_symlink('frisbee')
        await self.ensure_reset()
        await self.feedback('reboot', f"idling for {idle}s")
        await asyncio.sleep(idle)

    async def run_frisbee(self, ipaddr, port, reset):
        await self.wait_for_telnet('frisbee')
        self.manage_nextboot_symlink('cleanup')
        result = await self.frisbee.run(ipaddr, port)
        #logger.info(f"run_frisbee -> {result}")
        if reset:
            await self.ensure_reset()
        else:
            await self.feedback('reboot',
                                'skipping final reset')
        return result

    async def run_imagezip(self, port, reset, radical, comment):
        await self.wait_for_telnet('imagezip')
        self.manage_nextboot_symlink('cleanup')
        result = await self.imagezip.run(port, self.control_hostname(),
                                         radical, comment)
        #logger.info(f"run_imagezip -> {result}")
        if reset:
            await self.ensure_reset()
        else:
            await self.feedback('reboot',
                                'skipping final reset')
        return result
