#/usr/bin/env python3

from rhubarbe.config import Config

import os

class MisformedRange(Exception):
    def __init__(self, range):
        self.range = range

    def __str__(self):
        return "Misformed node range '{}'".format(self.range)

class Selector:

    # typically regularname='fit' and rebootname='reboot'
    # so that fit01 and reboot01 are names that resolve
    def __init__(self):
        the_config = Config()
        self.regularname = the_config.value('testbed', 'regularname')
        self.rebootname = the_config.value('testbed', 'rebootname')
        self.set = set()

    def __repr__(self):
        return "<Selector " + " ".join(self.node_names()) + ">"
    def __str__(self):
        return "Selected nodes: " + " ".join(self.node_names())

    def add_or_delete(self, index, add_if_true):
        if add_if_true:
            self.set.add(index)
        # set.remove triggers KeyError
        elif index in self.set:
            self.set.remove(index)

    # range is a shell arg, like fit01, fit1, 1, 1-12, ~25
    def add_range(self, range_spec):
        range_spec = range_spec.replace(self.regularname, "").replace(self.rebootname, "")
        commas = range_spec.split(',')
        for comma in commas:
            adding = True
            if comma.startswith('~'):
                adding = False
                comma = comma[1:]
            try:
                items = [ int(x) for x in comma.split('-')]
            except Exception as e:
                # safer to exit abruptly; common mistake is to forget -i, like in
                # rload image-radical 1 2 3
                # if we just ignore this situation, the wrong sentence
                # leads to a totally different behaviour
                raise MisformedRange(comma)
            if len(items) >= 4:
                print("Ignored arg {comma}".format(**locals()))
                continue
            elif len(items) == 3:
                a, b, c = items
                for i in range(a, b+1, c):
                    self.add_or_delete(i, adding)
            elif len(items) == 2:
                a, b = items
                for i in range(a, b+1):
                    self.add_or_delete(i, adding)
            else:
                i = items[0]
                self.add_or_delete(i, adding)

    # generators
    def node_names(self):
        return ("{}{:02}".format(self.regularname, i) for i in sorted(self.set))
    def cmc_names(self):
        return ("{}{:02}".format(self.rebootname, i) for i in sorted(self.set))

    def __len__(self):
        return len(self.set)
    def use_all_scope(self):
        the_config = Config()
        self.add_range(the_config.value('testbed', 'all_scope'))

####################
#convenience tools shared by all commands that need this sort of selection
# maybe we should have specialized ArgumentParser instead
def add_selector_arguments(arg_parser):
    arg_parser.add_argument(
        "-a", "--all-nodes", action='store_true', default=False,
        help="""
        add the contents of the testbed.all_scope config variable in the mix
        this can be combined with ranges, like e.g.
        -a ~4-16-2
        """)
    arg_parser.add_argument(
        "ranges", nargs="*",
        help="""
        nodes can be specified one by one, like: 1 004 fit01 reboot01;
        or by range (inclusive) like: 2-12, fit4-reboot12;
        or by range+step like: 4-16-2 which would be all even numbers from 4 to 16;
        ranges can also be excluded with '~', so ~1-4 means remove 1,2,3,4

        ex:  1-4 7-13-2 
        """)
        
# parser_args is the result of arg_parser.parse_args()
def selected_selector(parser_args, defaults_to_all=False):        
    ranges = parser_args.ranges
    
    # our naming conventions
    selector = Selector()
    # nothing set on the command line : let's use $NODES
    if parser_args.all_nodes:
        selector.use_all_scope()
    # nothing specified at all - no range no --all-nodes
    if not ranges and not parser_args.all_nodes:
        if defaults_to_all:
            selector.use_all_scope()
        elif os.getenv('NODES'):
            for node in os.environ["NODES"].split():
                selector.add_range(node)
        else:
            print("no argument specified : this requires you to set env. variable 'NODES'")
            exit(1)            
    else:
        for range in ranges:
            selector.add_range(range)

    return selector
