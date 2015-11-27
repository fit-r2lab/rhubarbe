#/usr/bin/env python3

import os.path

import asyncio
import aiohttp

from logger import logger
from inventory import the_inventory
from frisbee import Frisbee
from imagezip import ImageZip

class Node:

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

    def __repr__(self):
        return "<Node {}>".format(self.control_hostname())

    def is_known(self):
        return self.control_mac_address() is not None

    def control_mac_address(self):
        return the_inventory.attached_hostname_info(self.cmc_name, 'control', 'mac')

    def control_ip_address(self):
        return the_inventory.attached_hostname_info(self.cmc_name, 'control', 'ip')

    def control_hostname(self):
        return the_inventory.attached_hostname_info(self.cmc_name, 'control', 'hostname')

    @asyncio.coroutine
    def get_status(self):
        """
        updates self.status
        either 'on' or 'off', or None if something wrong is going on
        """
        url = "http://{}/status".format(self.cmc_name)
        try:
            client_response = yield from aiohttp.get(url)
        except Exception as e:
            self.status = None
        try:
            text = yield from client_response.text()
            self.status = text.strip()
        except Exception as e:
            self.status = None

    ####################
    # what status to expect after a message is sent
    expected_map = {
        'on' : 'on',
        'reset' : 'on',
        'off' : 'off'
    }
        
    @asyncio.coroutine
    def send_action(self, message="on", check = False, check_delay=1.):
        """
        Actually send action message like 'on', 'off' or 'reset'
        if check is True, waits for check_delay seconds before checking again that
        the status is what is expected, i.e.
        | message  | expected |
        |----------|----------|
        | on,reset | on       |
        | off      | off      |

        return value stored in self.action
        * if check is false
          * True if request can be sent and returns 'ok', or None if something goes wrong
        * otherwise:
          * True to indicate that the node is correctly in 'on' mode after checking
          * False to indicate that the node is 'off' after checking
          * None if something goes wrong
        """
        url = "http://{}/{}".format(self.cmc_name, message)
        try:
            client_response = yield from aiohttp.get(url)
        except Exception as e:
            self.action = None
            return self

        try:
            text = yield from client_response.text()
            ok = text.strip() == 'ok'
        except Exception as e:
            self.action = None
            return self
    
        if not check:
            self.action = ok
            return self
        else:
            yield from asyncio.sleep(check_delay)
            yield from self.get_status()
            self.action = self.status == self.expected_map[message]
            return self

    ####################
    message_to_reset_map = { 'on' : 'reset', 'off' : 'on' }

    @asyncio.coroutine
    def feedback(self, field, message):
        yield from self.message_bus.put(
            {'ip': self.control_ip_address(), field: message})

    @asyncio.coroutine
    def ensure_reset(self):
        if self.status is None:
            yield from self.get_status()
        if self.status not in self.message_to_reset_map:
            yield from self.feedback('reboot', "Cannot get status at {}".format(self.cmc_name))
        message_to_send = self.message_to_reset_map[self.status]
        yield from self.feedback('reboot', "Sending message '{}' to CMC {}"
                                 .format(message_to_send, self.cmc_name))
        yield from self.send_action(message_to_send, check=True)
        if not self.action:
            yield from self.feedback("Failed to send message {} to CMC {}"
                                     .format(message_to_send, self.cmc_name))


    ##########
    # used to be a coroutine but as we need this when dealing by KeybordInterrupt
    # it appears much safer to just keep this a traditional function
    def manage_nextboot_symlink(self, action):
        """
        Messes with the symlink in /tftpboot/pxelinux.cfg/
        Depending on 'action'
        * 'cleanup' or 'harddrive' : clear the symlink corresponding to this CMC
        * 'frisbee' : define a symlink so that next boot will run the frisbee image
        see rhubarbe.conf for configurable options
        """

        from config import the_config
        root = the_config.value('pxelinux', 'config_dir')
        frisbee = the_config.value('pxelinux', 'frisbee_image')
        
        # of the form 01-00-03-1d-0e-03-53
        mylink = "01-" + self.control_mac_address().replace(':', '-')
        source = os.path.join(root, mylink)
        dest = os.path.join(root, frisbee)

        if action in ('cleanup', 'harddrive'):
            if os.path.exists(source):
                logger.info("Removing {}".format(source))
                os.remove(source)
        elif action in ('frisbee'):
            if os.path.exists(source):
                os.remove(source)
            logger.info("Creating {}".format(source))
            os.symlink(frisbee, source)
        else:
            logger.critical("manage_nextboot_symlink : unknown action {}".format(action))

    ##########
    @asyncio.coroutine
    def wait_for_telnet(self, service):
        ip = self.control_ip_address()
        if service == 'frisbee':
            self.frisbee = Frisbee(ip, self.message_bus)
            yield from self.frisbee.wait()
        elif service == 'imagezip':
            self.imagezip = ImageZip(ip, self.message_bus)
            yield from self.imagezip.wait()
            pass
        
    @asyncio.coroutine
    def load_stage1(self, idle):
        self.manage_nextboot_symlink('frisbee')
        yield from self.ensure_reset()
        yield from self.feedback('reboot', "idling for {}s".format(idle))
        yield from asyncio.sleep(idle)

    @asyncio.coroutine
    def load_stage2(self, ip , port, reset):
        yield from self.wait_for_telnet('frisbee')
        self.manage_nextboot_symlink('cleanup')
        yield from self.frisbee.run(ip, port)
        if reset:
            yield from self.ensure_reset()
        else:
            yield from self.feedback('reboot', 'skipping final reset')

    # incidentally, same stages for both loading and saving
    save_stage1 = load_stage1

    @asyncio.coroutine
    def save_stage2(self, port, reset):
        yield from self.wait_for_telnet('imagezip')
        yield from self.imagezip.run(port)
        if reset:
            yield from self.ensure_reset()
        else:
            yield from self.feedback('reboot', 'skipping final reset')
