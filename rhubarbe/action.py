"""
utility to send a given CMC action to a set of nodes
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w1202,r1705

from asynciojobs import Job, Scheduler

from rhubarbe.node import Node
from rhubarbe.display import Display


class Action:

    verb_to_method = {
        'status': 'get_status',
        'on': 'turn_on',
        'off': 'turn_off',
        'reset': 'do_reset',
        'info': 'get_info',
        'usrpstatus': 'get_usrpstatus',
        'usrpon': 'turn_usrpon',
        'usrpoff': 'turn_usrpoff',
        'bothoff': 'turn_both_off',
    }

    def __init__(self, verb, selector):
        self.verb = verb
        self.selector = selector

    async def get_and_show_verb(self, node, verb):
        assert verb in Action.verb_to_method
        # send the 'verb' method on node
        method = getattr(node, Action.verb_to_method[verb])
        # bound methods must not be passed the subject !
        result = await method()
        result = result if result is not None else f"{verb} N/A"
        for line in result.split("\n"):
            if line:
                print(f"{node.cmc_name}:{line}")

    # would make more sense to define this as a coroutine..
    def run(self, message_bus, timeout):
        """
        send verb to all nodes, waits for max timeout
        returns True if all nodes behaved as expected
        and False otherwise - including in case of KeyboardInterrupt
        """

        nodes = [Node(cmc_name, message_bus)
                 for cmc_name in self.selector.cmc_names()]
        jobs = [Job(self.get_and_show_verb(node, self.verb), critical=True)
                for node in nodes]
        display = Display(nodes, message_bus)
        scheduler = Scheduler(Job(display.run(), forever=True, critical=True),
                              *jobs,
                              timeout=timeout,
                              critical=False)
        try:
            if scheduler.run():
                return True
            else:
                scheduler.debrief(silence_done_jobs=True)
                print(f"rhubarbe-{self.verb} failed: {scheduler.why()}")
                return False
        except KeyboardInterrupt:
            print(f"rhubarbe-{self.verb} : keyboard interrupt - exiting")
            return False
