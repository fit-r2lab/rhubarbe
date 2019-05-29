"""
display class when using curses, like in
rhubarbe load --curses
"""

import curses

from rhubarbe.display import Display

# c0111 no docstrings yet
# w0201 attributes defined outside of __init__
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w0201,r1705


class DisplayCurses(Display):

    # extra room on top and on the left
    offsetl = 3
    offsetc = 25

    def start_hook(self):
        self.screen = curses.initscr()
        self.maxl, self.maxc = self.screen.getmaxyx()
        self.submaxc = self.maxc - self.offsetc
        if len(self.nodes) + self.offsetl + 2 >= self.maxl:
            self.screen.addstr(1, 1,
                               "WARNING: screen not high enough - "
                               "best effort..")
            self.screen.refresh()
            self.submaxl = self.maxl - self.offsetl
        else:
            self.submaxl = len(self.nodes) + 2
        self.usable_l = self.submaxl - 1
        self.screen.border()
        self.subwin = self.screen.subwin(self.submaxl, self.submaxc,
                                         self.offsetl, self.offsetc)
        self.subwin.border()

    # this is guaranteed to run once the event loop has returned
    # and all the messages have been displayed
    def epilogue(self):
        prompt = "Press any key to exit"
        if self.goodbye_message:
            prompt = self.goodbye_message + " " + prompt
        self.screen.addstr(self.maxl-1, 1, prompt)
        self.screen.refresh()
        self.screen.getch()
        curses.endwin()

    def repair(self):
        curses.endwin()

    def dispatch_hook(self, message, timestamp, duration):
        timemsg = f"{timestamp} {duration}"
        text = self.message_to_text(message)
        self.screen.addstr(1, 1, timemsg)
        self.screen.addstr(1, self.offsetc+1, self.pad(text))
        self.screen.refresh()

    def dispatch_ip_hook(self, _, node,            # pylint:disable=w0221,r0913
                         message, timestamp, duration):
        timemsg = f"{timestamp} {duration} {node.name}"
        text = self.message_to_text_ip(message, node, mention_node=False)
        text = str(text)
        line = (node.rank % self.usable_l) + 1
        self.screen.addstr(line+self.offsetl, 1, timemsg)
        self.subwin.addstr(line, 1, self.pad(text))
        self.screen.refresh()
        self.subwin.refresh()

    def dispatch_ip_percent_hook(self, _, node,    # pylint:disable=w0221,r0913
                                 message, timestamp, duration):
        # global area
        timemsg = f"{timestamp} {duration}"
        text = self.node_percent_bar(int((self.total_percent)/len(self.nodes)))
        self.screen.addstr(2, 1, timemsg)
        self.screen.addstr(2, self.offsetc+1, self.pad(text))
        # node area
        timemsg = f"{timestamp} {duration} {node.name}"
        barsize = self.node_percent_bar(node.percent)
        line = (node.rank % self.usable_l) + 1
        self.screen.addstr(line+self.offsetl, 1, timemsg)
        self.subwin.addstr(line, 1, barsize)
        self.screen.refresh()
        self.subwin.refresh()

    def node_percent_bar(self, percent):
        # 2 is for the 2 borders left and right; 4 is the size for '|10%'
        avail = self.submaxc - 2 - 4
        left = int(percent*avail/100)
        middle = avail - left
        right = f"|{percent:02}%" if percent != 100 else "||||"
        return left*'#' + middle*' ' + right

#    def pad(self, text):
#        return self._pad(text, self.maxc-2)
    def pad(self, text):
        return self._pad(text, self.submaxc-2)

    @staticmethod
    def _pad(text, width):
        if len(text) == width:
            return text
        elif len(text) > width:
            return text[:(width-3)] + '...'
        else:
            missing = width-len(text)
            return text + missing*' '
