#!/usr/bin/env python3

import asyncio

from argparse import ArgumentParser

from rhubarbe.selector import Selector, add_selector_arguments, selected_selector
from rhubarbe.display import Display
from rhubarbe.display_curses import DisplayCurses
from rhubarbe.node import Node
from rhubarbe.imageloader import ImageLoader
from rhubarbe.imagesaver import ImageSaver
from rhubarbe.monitor import Monitor

from rhubarbe.ssh import SshProxy
from rhubarbe.leases import Leases
from rhubarbe.logger import logger
import rhubarbe.util as util

# a supported command comes with a function in this module
# that takes a list of args 
# and returns 0 for success and s/t else otherwise
# specifically, command
# rhubarbe load -i fedora 12
# would result in a call
# load ( [ "-i", "fedora", "12" ])
supported_commands = [ 'load', 'save', 'status', 'wait', 'list', 'monitor', 'version' ]

####################
def load(argv):
    from rhubarbe.config import the_config
    from rhubarbe.imagesrepo import the_imagesrepo
    default_image = the_imagesrepo.default()
    default_timeout = the_config.value('nodes', 'load_default_timeout')
    default_bandwidth = the_config.value('networking', 'bandwidth')
                            
    parser = ArgumentParser()
    parser.add_argument("-i", "--image", action='store', default=default_image,
                        help="Specify image to load (default is {})".format(default_image))
    parser.add_argument("-t", "--timeout", action='store', default=default_timeout, type=float,
                        help="Specify global timeout for the whole process, default={}"
                              .format(default_timeout))
    parser.add_argument("-b", "--bandwidth", action='store', default=default_bandwidth, type=int,
                        help="Set bandwidth in Mibps for frisbee uploading - default={}"
                              .format(default_bandwidth))
    parser.add_argument("-c", "--curses", action='store_true', default=False)
    # this is more for debugging
    parser.add_argument("-n", "--no-reset", dest='reset', action='store_false', default=True,
                        help = """use this with nodes that are already running a frisbee image.
                        They won't get reset, neither before or after the frisbee session
                        """)
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    message_bus = asyncio.Queue()

    selector = selected_selector(args)
    if not selector.how_many():
        parser.print_help()
        return 1
    nodes = [ Node(cmc_name, message_bus) for cmc_name in selector.cmc_names() ]

    # send feedback
    message_bus.put_nowait({'selected_nodes' : selector})
    logger.info("timeout is {}".format(args.timeout))
    logger.info("bandwidth is {}".format(args.bandwidth))

    actual_image = the_imagesrepo.locate(args.image)
    if not actual_image:
        print("Image file {} not found - emergency exit".format(args.image))
        exit(1)

    # send feedback
    message_bus.put_nowait({'loading_image' : actual_image})
    display_class = Display if not args.curses else DisplayCurses
    display = display_class(nodes, message_bus)
    loader = ImageLoader(nodes, image=actual_image, bandwidth=args.bandwidth,
                         message_bus=message_bus, display=display)
    return loader.main(reset=args.reset, timeout=args.timeout)
 
####################
def save(argv):
    from rhubarbe.config import the_config
    default_timeout = the_config.value('nodes', 'save_default_timeout')

    parser = ArgumentParser()
    parser.add_argument("-o", "--output", action='store', default=None,
                        help="Specify output name for image")
    parser.add_argument("-t", "--timeout", action='store', default=default_timeout, type=float,
                        help="Specify global timeout for the whole process, default={}"
                              .format(default_timeout))
#    parser.add_argument("-c", "--curses", action='store_true', default=False)
    parser.add_argument("-n", "--no-reset", dest='reset', action='store_false', default=True,
                        help = """use this with nodes that are already running a frisbee image.
                        They won't get reset, neither before or after the frisbee session
                        """)
    parser.add_argument("node")
    args = parser.parse_args(argv)

    message_bus = asyncio.Queue()

    selector = Selector()
    selector.add_range(args.node)
    # in case there was one argument but it was not found in inventory
    if selector.how_many() != 1:
        parser.print_help()
    cmc_name = next(selector.cmc_names())
    node = Node(cmc_name, message_bus)
    nodename = node.control_hostname()
    
    from rhubarbe.imagesrepo import the_imagesrepo
    actual_image = the_imagesrepo.where_to_save(nodename, args.output)
    message_bus.put_nowait({'info' : "Saving image {}".format(actual_image)})
# turn off curses mode that has no added value here
# the progressbar won't show too well anyway
#    display_class = Display if not args.curses else DisplayCurses
    display_class = Display
    display = display_class([node], message_bus)
    saver = ImageSaver(node, image=actual_image,
                       message_bus=message_bus, display = display)
    return saver.main(reset = args.reset, timeout=args.timeout)

####################
def status(argv):
    from rhubarbe.config import the_config
    default_timeout = the_config.value('nodes', 'status_default_timeout')
    
    parser = ArgumentParser()
    parser.add_argument("-t", "--timeout", action='store', default=default_timeout, type=float,
                        help="Specify global timeout for the whole process, default={}"
                              .format(default_timeout))
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args)
    message_bus = asyncio.Queue()
    
    message_bus.put_nowait({'selected_nodes' : selector})
    logger.info("timeout is {}".format(args.timeout))

    loop = asyncio.get_event_loop()
    nodes = [ Node(cmc_name, message_bus) for cmc_name in selector.cmc_names() ]
    coros = [ node.get_status() for node in nodes ]
    
    tasks = util.self_manage(asyncio.gather(*coros))
    wrapper = asyncio.wait_for(tasks, timeout = args.timeout)
    try:
        loop.run_until_complete(wrapper)
        for node in nodes:
            print("{}:{}".format(node.cmc_name, node.status))
        return 0
    except KeyboardInterrupt as e:
        print("rhubarbe-status : keyboard interrupt - exiting")
        tasks.cancel()
        loop.run_forever()
        tasks.exception()
        return 1
    except asyncio.TimeoutError as e:
        print("rhubarbe-status : timeout expired after {}s".format(args.timeout))
        return 1
    finally:
        loop.close()

####################
def wait(argv):
    from rhubarbe.config import the_config
    default_timeout = the_config.value('nodes', 'wait_default_timeout')
    default_backoff = the_config.value('networking', 'ssh_backoff')
    
    parser = ArgumentParser()
    parser.add_argument("-t", "--timeout", action='store', default=default_timeout, type=float,
                        help="Specify global timeout for the whole process, default={}"
                              .format(default_timeout))
    parser.add_argument("-b", "--backoff", action='store', default=default_backoff, type=float,
                        help="Specify backoff average for between attempts to ssh connect, default={}"
                              .format(default_backoff))
# iwait/curses won't work too well - turned off for now
#    parser.add_argument("-c", "--curses", action='store_true', default=False)
    parser.add_argument("-v", "--verbose", action='store_true', default=False)

    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args)
    message_bus = asyncio.Queue()

    if args.verbose:
        message_bus.put_nowait({'selected_nodes' : selector})
    logger.info("timeout is {}".format(args.timeout))

    loop = asyncio.get_event_loop()
    nodes = [ Node(cmc_name, message_bus) for cmc_name in selector.cmc_names() ]
    sshs =  [ SshProxy(node, verbose=args.verbose) for node in nodes ]
    coros = [ ssh.wait_for(args.backoff) for ssh in sshs ]

# iwait/curses won't work too well - turned off for now
#    display_class = Display if not args.curses else DisplayCurses
    display_class = Display
    display = display_class(nodes, message_bus)

    @asyncio.coroutine
    def run():
        yield from asyncio.gather(*coros)
        yield from display.stop()

    t1 = util.self_manage(run())
    t2 = util.self_manage(display.run())
    tasks = asyncio.gather(t1, t2)
    wrapper = asyncio.wait_for(tasks, timeout = args.timeout)
    try:
        loop.run_until_complete(wrapper)
        return 0
    except KeyboardInterrupt as e:
        print("rhubarbe-wait : keyboard interrupt - exiting")
        tasks.cancel()
        loop.run_forever()
        tasks.exception()
        return 1
    except asyncio.TimeoutError as e:
        print("rhubarbe-wait : timeout expired after {}s".format(args.timeout))
        return 1
    finally:
        for ssh in sshs:
            print("{}:{}".format(ssh.node, ssh.status))
        loop.close()
        
####################
def list(argv):
    from rhubarbe.config import the_config
    parser = ArgumentParser()
    parser.add_argument("-c", "--config", action='store_true', default=False,
                        help="display configuration store")
    parser.add_argument("-i", "--images", action='store_true', default=False,
                        help="display available images")
    parser.add_argument("-n", "--inventory", action='store_true', default=False,
                        help="display nodes from inventory")
    parser.add_argument("-s", "--sort", dest='sort_by', action='store', default='size',
                        choices=('date', 'size'),
                        help="sort by date or by size")
    parser.add_argument("-r", "--reverse", action='store_true', default=False,
                        help="reverse sort")
    parser.add_argument("-a", "--all", action='store_true', default=False)
    args = parser.parse_args(argv)

    if args.config or args.all:
        the_config.display()
    if args.images or args.all:
        from rhubarbe.imagesrepo import the_imagesrepo
        the_imagesrepo.display(args.sort_by, args.reverse)
    if args.inventory or args.all:
        from rhubarbe.inventory import the_inventory
        the_inventory.display(verbose=True)
    return 0

####################
def monitor(argv):
    from rhubarbe.config import the_config
    default_cycle = the_config.value('monitor', 'cycle')
    parser = ArgumentParser()
    parser.add_argument('-c', "--cycle", default=default_cycle, type=float,
                        help="Delay to wait between 2 probes of each node, default ={}"
                        .format(default_cycle))
    parser.add_argument("-w", "--no-wlan", dest="report_wlan", default=True, action='store_true',
                        help="avoid probing of wlan traffic rates")
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args)
    loop = asyncio.get_event_loop()
    message_bus = asyncio.Queue()

    # xxx having to feed a Display instance with nodes
    # at creation time is a nuisance
    display = Display([], message_bus)

    message_bus.put_nowait({'selected_nodes' : selector})
    monitor = Monitor(selector.cmc_names(),
                      message_bus = message_bus,
                      cycle = args.cycle,
                      report_wlan=args.report_wlan)

    @asyncio.coroutine
    def run():
        yield from monitor.run()
        yield from display.stop()

    t1 = util.self_manage(run())
    t2 = util.self_manage(display.run())
    tasks = asyncio.gather(t1, t2)
    wrapper = asyncio.gather(tasks)
    try:
        loop.run_until_complete(wrapper)
        return 0
    except KeyboardInterrupt as e:
        print("rhubarbe-wait : keyboard interrupt - exiting")
        tasks.cancel()
        loop.run_forever()
        tasks.exception()
        return 1
    except asyncio.TimeoutError as e:
        print("rhubarbe-wait : timeout expired after {}s".format(args.timeout))
        return 1
    finally:
        loop.close()
    

####################
def version(argv):
    from rhubarbe.version import version
    print("rhubarbe version {}".format(version))
    return 0
