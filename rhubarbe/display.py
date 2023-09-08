"""
display class when not using curses, like in
rhubarbe load
"""

import time

# pip3 install progressbar33
import progressbar

from rhubarbe.logger import logger

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# r0913 too many arguments in functions
# pylint: disable=c0111,w1202,w0201,r1705,r0913


# message_bus is just an asyncio.Queue

# a display instance comes with a hash
# 'ip' -> DisplayNode
# in its simplest form a DisplayNode just has
# a hostname (retrieved from ip through inventory)
# a rank in the nodes list (starting at 0)
# a percentage

class DisplayNode:                                      # pylint: disable=r0903
    def __init__(self, name, rank):
        self.name = name
        self.rank = rank
        self.percent = 0


class Display:                                          # pylint: disable=r0902
    def __init__(self, nodes, message_bus):
        self.message_bus = message_bus
        self.nodes = nodes
        #
        self._alive = True
        self._start_time = None
        # this will go from 0 to 100*len(self.nodes)
        self.total_percent = 0
        # correspondance ip -> display_node
        self._display_node_by_ip = {}
        self.goodbye_message = None
        # for the basic displaying : we use a ingle global progress bar
        self.pbar = None

    def get_display_node(self, ipaddr):
        # if we have it already
        if ipaddr in self._display_node_by_ip:
            return self._display_node_by_ip[ipaddr]

        # in case the incoming ip is a reboot ip
        from rhubarbe.inventorynodes import InventoryNodes
        the_inventory = InventoryNodes()
        control_ip = the_inventory.control_ip_from_any_ip(ipaddr)
        # locate this in the subject nodes list
        for rank, node in enumerate(self.nodes):
            if node.control_ip_address() == control_ip:
                self._display_node_by_ip[ipaddr] = \
                    DisplayNode(node.control_hostname(), rank)
                return self._display_node_by_ip[ipaddr]
        return None

    async def run(self):
        self.start_hook()
        self._start_time = time.time()

        while self._alive:
            message = await self.message_bus.get()
            if message == 'END-DISPLAY':
                self._alive = False
                break
            self.dispatch(message)
            # this is new in 3.4.4
            if 'task_done' in dir(self.message_bus):
                self.message_bus.task_done()

    async def stop(self):
        # soft stop
        await self.message_bus.put("END-DISPLAY")

#    def stop_nowait(self):
#        if self._alive:
#            self._alive = False
#            self.stop_hook()

    def dispatch(self, message):
        timestamp = time.strftime("%H:%M:%S")
        # in case the message is sent before the event loop has started
        duration = (f"+{int(time.time()-self._start_time):03}s"
                    if self._start_time is not None else 5*'-')
        if isinstance(message, dict) and 'ip' in message:
            ipaddr = message['ip']
            node = self.get_display_node(ipaddr)
            if node is None:
                logger.info(f"Unexpected message gave node=None in dispatch: {message}")
            elif 'tick' in message:
                self.dispatch_ip_tick_hook(ipaddr, node, message,
                                           timestamp, duration)
            elif 'percent' in message:
                # compute delta, update node.percent and self.total_percent
                node_previous_percent = node.percent
                node_current_percent = message['percent']
                delta = node_current_percent - node_previous_percent
                node.percent = node_current_percent
                self.total_percent += delta
                logger.info(f"{node.name} percent: {node_current_percent}/100 "
                            f"(was {node_previous_percent}), "
                            f"total {self.total_percent}/{100*len(self.nodes)}")
                self.dispatch_ip_percent_hook(ipaddr, node, message,
                                              timestamp, duration)
            else:
                self.dispatch_ip_hook(ipaddr, node, message,
                                      timestamp, duration)
        else:
            self.dispatch_hook(message, timestamp, duration)

    @staticmethod
    def message_to_text(message):
        if isinstance(message, str):
            return message
        elif not isinstance(message, dict):
            # should not happen
            return "UNEXPECTED" + str(message)
        elif 'info' in message:
            return message['info']
        elif 'authorization' in message:
            return "AUTH: " + message['authorization']
        elif 'loading_image' in message:
            return f"Loading image {message['loading_image']}"
        elif 'selected_nodes' in message:
            names = message['selected_nodes'].node_names()
            return ("Selection: " + " ".join(names)) \
                if names \
                else "Empty Node Selection"
        else:
            return str(message)

    subkeys = ['frisbee_retcod', 'reboot', 'ssh_status', 'frisbee_status']

    def message_to_text_ip(self, message, node, mention_node=True):
        text = None
        if 'percent' in message:
            text = f"{message['percent']:02}"
        elif 'frisbee_retcod' in message:
            text = "Uploading successful" \
                if message['frisbee_retcod'] == 0 \
                else "Uploading FAILED !"
        else:
            for key in self.subkeys:
                if key in message:
                    text = f"{key} = {message[key]}"
                    break
        if text is None:
            text = str(message)
        return text \
            if not mention_node \
            else f"{node.name} : {text}"

    def set_goodbye(self, message):
        self.goodbye_message = message

    ####################
    # specifics of the basic display
    def start_hook(self):
        pass

    def epilogue(self):
        if self.goodbye_message:
            print(self.goodbye_message)

    def repair(self):
        self.epilogue()

    def dispatch_hook(self, message, timestamp, duration):
        text = self.message_to_text(message)
        print(f"{timestamp} - {duration}: {text}")

    def dispatch_ip_hook(self, ipaddr, node,            # pylint: disable=w0613
                         message, timestamp, duration):
        text = self.message_to_text_ip(message, node, mention_node=False)

        print(f"{timestamp} - {duration}: {node.name} {text}")

    def dispatch_ip_percent_hook(self, ipaddr, node,    # pylint: disable=w0613
                                 message, timestamp,    # pylint: disable=w0613
                                 duration):             # pylint: disable=w0613
        # start progressbar
        if self.pbar is None:
            widgets = [
                progressbar.Bar(),
                progressbar.Percentage(), ' |',
                progressbar.FormatLabel('%(seconds).2fs'), '|',
                progressbar.ETA(),
            ]
            self.pbar = \
                progressbar.ProgressBar(widgets=widgets,
                                        maxval=len(self.nodes)*100)
            self.pbar.start()
        self.pbar.update(self.total_percent)
        if self.total_percent == len(self.nodes)*100:
            self.pbar.finish()

    def dispatch_ip_tick_hook(self, ipaddr, node,       # pylint: disable=w0613
                              message, timestamp,       # pylint: disable=w0613
                              duration):                # pylint: disable=w0613
        # start progressbar
        if self.pbar is None:
            widgets = [
                'Collecting image : ',
                # progressbar.BouncingBar(marker=progressbar.RotatingMarker()),
                progressbar.BouncingBar(marker='*'),
                progressbar.FormatLabel(' %(seconds).2fs'),
            ]
            self.pbar = \
                progressbar.ProgressBar(widgets=widgets,
                                        maxval=progressbar.UnknownLength)
            self.pbar.start()
            # progressbar is willing to work as expected here
            # with maxval=UnknownLength
            # but still insists to get a real value apparently
            self.value = 0
        self.value += 1
        self.pbar.update(self.value)
        # hack way to finish the progressbar
        # since we have no other way to figure it out
        if message['tick'] == 'END':
            self.pbar.finish()
