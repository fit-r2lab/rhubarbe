#!/usr/bin/env python3

import asyncio

from argparse import ArgumentParser
from logger import logger

from monitor import Monitor
from monitor_curses import MonitorCurses

from node import Node
from selector import Selector, add_selector_arguments, selected_selector
from imageloader import ImageLoader
from imagesaver import ImageSaver
from imagesrepo import the_imagesrepo
from config import the_config
from ssh import SshProxy
import util
from leases import Leases

# for each of these there should be a symlink to rhubarbe-main.py
# like rhubarbe-load -> rhubarbe-main.py
# and a function in this module with no arg
supported_commands = [ 'load', 'save', 'status', 'wait', 'list' ]

####################
def load():
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
    args = parser.parse_args()

    message_bus = asyncio.Queue()

    selector = selected_selector(args)
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
    monitor_class = Monitor if not args.curses else MonitorCurses
    monitor = monitor_class(nodes, message_bus)
    loader = ImageLoader(nodes, image=actual_image, bandwidth=args.bandwidth,
                         message_bus=message_bus, monitor=monitor)
    return loader.main(reset=args.reset, timeout=args.timeout)
 
####################
def save():
    default_timeout = the_config.value('nodes', 'save_default_timeout')

    parser = ArgumentParser()
    parser.add_argument("-o", "--output", action='store', default=None,
                        help="Specify output name for image")
    parser.add_argument("-t", "--timeout", action='store', default=default_timeout, type=float,
                        help="Specify global timeout for the whole process, default={}"
                              .format(default_timeout))
    parser.add_argument("-c", "--curses", action='store_true', default=False)
    parser.add_argument("-n", "--no-reset", dest='reset', action='store_false', default=True,
                        help = """use this with nodes that are already running a frisbee image.
                        They won't get reset, neither before or after the frisbee session
                        """)
    parser.add_argument("node")
    args = parser.parse_args()

    message_bus = asyncio.Queue()

    selector = Selector()
    selector.add_range(args.node)
    print(selector)
    # in case there was one argument but it was not found in inventory
    if selector.how_many() != 1:
        parser.print_help()
    cmc_name = next(selector.cmc_names())
    node = Node(cmc_name, message_bus)
    nodename = node.control_hostname()
    
    actual_image = the_imagesrepo.where_to_save(nodename, args.output)
    message_bus.put_nowait({'saving_image' : actual_image})
    monitor_class = Monitor if not args.curses else MonitorCurses
    monitor = monitor_class([node], message_bus)
    saver = ImageSaver(node, image=actual_image,
                       message_bus=message_bus, monitor = monitor)
    return saver.main(reset = args.reset, timeout=args.timeout)

####################
def status():
    default_timeout = the_config.value('nodes', 'status_default_timeout')
    
    parser = ArgumentParser()
    parser.add_argument("-t", "--timeout", action='store', default=default_timeout, type=float,
                        help="Specify global timeout for the whole process, default={}"
                              .format(default_timeout))
    add_selector_arguments(parser)
    args = parser.parse_args()

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
def wait():
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
    args = parser.parse_args()

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
#    monitor_class = Monitor if not args.curses else MonitorCurses
    monitor_class = Monitor
    monitor = monitor_class(nodes, message_bus)

    @asyncio.coroutine
    def run():
        yield from asyncio.gather(*coros)
        yield from monitor.stop()

    t1 = util.self_manage(run())
    t2 = util.self_manage(monitor.run())
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
def list():
    parser = ArgumentParser()
    parser.add_argument("-c", "--config", action='store_true', default=False,
                        help="display configuration store")
    parser.add_argument("-i", "--images", action='store_true', default=False,
                        help="display available images")
    parser.add_argument("-n", "--inventory", action='store_true', default=False,
                        help="display nodes from inventory")
    parser.add_argument("-a", "--all", action='store_true', default=False)
    args = parser.parse_args()

    if args.config or args.all:
        the_config.display()
    if args.images or args.all:
        from imagesrepo import the_imagesrepo
        the_imagesrepo.display()
    if args.inventory or args.all:
        from inventory import the_inventory
        the_inventory.display(verbose=True)
    return 0

####################
####################
####################
import sys

def main():
    command=sys.argv[0]
    for supported in supported_commands:
        if supported in command:
            main_function = globals()[supported]
            exit(main_function())
    print("Unknown command {}", command)

if __name__ == '__main__':
    main()
