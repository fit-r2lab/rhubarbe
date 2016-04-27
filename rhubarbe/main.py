#!/usr/bin/env python3

# DON'T import logger globally here
# we need to be able to mess inside the logger module
# before it gets loaded from another way

import asyncio

from argparse import ArgumentParser

from rhubarbe.config import Config
from rhubarbe.imagesrepo import ImagesRepo
from rhubarbe.selector import Selector, add_selector_arguments, selected_selector
from rhubarbe.display import Display
from rhubarbe.display_curses import DisplayCurses
from rhubarbe.node import Node
from rhubarbe.imageloader import ImageLoader
from rhubarbe.imagesaver import ImageSaver
from rhubarbe.monitor import Monitor
from rhubarbe.ssh import SshProxy
from rhubarbe.leases import Leases

import rhubarbe.util as util

# a supported command comes with a driver function
# in this module, that takes a list of args 
# and returns 0 for success and s/t else otherwise
# specifically, command
# rhubarbe load -i fedora 12
# would result in a call
# load ( "-i", "fedora", "12" )

####################
# NOTE: when adding a new command, please update setup.py as well
supported_subcommands = []

def subcommand(driver):
    supported_subcommands.append(driver.__name__)
    return driver

####################
@subcommand
def nodes(*argv):
    usage = """
    Just display the hostnames of selected nodes
    """
    parser = ArgumentParser(usage=usage)
    add_selector_arguments(parser)
    args = parser.parse_args(argv)
    selector = selected_selector(args)
    print(" ".join(selector.node_names()))
    
####################
def cmc_verb(verb, *argv):
    usage = """
    Send verb '{verb}' to the CMC interface of selected nodes
    """.format(verb=verb)
    the_config = Config()
    default_timeout = the_config.value('nodes', 'cmc_default_timeout')
    
    parser = ArgumentParser(usage=usage)
    parser.add_argument("-t", "--timeout", action='store', default=default_timeout, type=float,
                        help="Specify global timeout for the whole process, default={}"
                              .format(default_timeout))
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args)
    message_bus = asyncio.Queue()
    
    from rhubarbe.logger import logger
    message_bus.put_nowait({'selected_nodes' : selector})
    logger.info("timeout is {}".format(args.timeout))

    loop = asyncio.get_event_loop()
    nodes = [ Node(cmc_name, message_bus) for cmc_name in selector.cmc_names() ]
    if verb == 'status':
        coros = [ node.get_status() for node in nodes ]
    elif verb == 'on':
        coros = [ node.turn_on() for node in nodes ]
    elif verb == 'off':
        coros = [ node.turn_off() for node in nodes ]
    elif verb == 'reset':
        coros = [ node.do_reset() for node in nodes ]
    elif verb == 'info':
        coros = [ node.get_info() for node in nodes ]
    
    tasks = util.self_manage(asyncio.gather(*coros))
    wrapper = asyncio.wait_for(tasks, timeout = args.timeout)
    try:
        loop.run_until_complete(wrapper)
        for node in nodes:
            result = getattr(node, verb)
            # protection just in case
            if result is None: result = ""
            for line in result.split("\n"):
                if line:
                    print("{}:{}".format(node.cmc_name, line))
        return 0
    except KeyboardInterrupt as e:
        print("rhubarbe-cmc : keyboard interrupt - exiting")
        tasks.cancel()
        loop.run_forever()
        tasks.exception()
        return 1
    except asyncio.TimeoutError as e:
        print("rhubarbe-cmc : timeout expired after {}s".format(args.timeout))
        return 1
    finally:
        loop.close()

#####
@subcommand
def status(*argv):   return cmc_verb('status', *argv)
@subcommand
def on(*argv):   return cmc_verb('on', *argv)
@subcommand
def off(*argv):   return cmc_verb('off', *argv)
@subcommand
def reset(*argv):   return cmc_verb('reset', *argv)
@subcommand
def info(*argv):   return cmc_verb('info', *argv)

####################
@subcommand
def load(*argv):
    usage = """
    Load an image on selected nodes in parallel
    Requires a valid lease - or to be root
    """
    the_config = Config()
    the_imagesrepo = ImagesRepo()
    default_image = the_imagesrepo.default()
    default_timeout = the_config.value('nodes', 'load_default_timeout')
    default_bandwidth = the_config.value('networking', 'bandwidth')
                            
    parser = ArgumentParser(usage=usage)
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
    from rhubarbe.logger import logger
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
@subcommand
def save(*argv):
    usage = """
    Save an image from a node
    Requires a valid lease - or to be root
    """

    the_config = Config()
    default_timeout = the_config.value('nodes', 'save_default_timeout')

    parser = ArgumentParser(usage=usage)
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
    
    the_imagesrepo = ImagesRepo()
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
@subcommand
def wait(*argv):
    usage = """
    Wait for selected nodes to be reachable by ssh
    Returns 0 if all nodes indeed are reachable
    """
    the_config = Config()
    default_timeout = the_config.value('nodes', 'wait_default_timeout')
    default_backoff = the_config.value('networking', 'ssh_backoff')
    
    parser = ArgumentParser(usage=usage)
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
    from rhubarbe.logger import logger
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
@subcommand
def monitor(*argv):

    ### xxx hacky - do a side effect in the logger module
    import rhubarbe.logger
    rhubarbe.logger.logger = rhubarbe.logger.monitor_logger
    ### xxx hacky
    
    usage = """
    Cyclic probe all selected nodes to report real-time status 
    at a sidecar service over socketIO
    """
    the_config = Config()
    default_cycle = the_config.value('monitor', 'cycle_status')
    parser = ArgumentParser(usage=usage)
    parser.add_argument('-c', "--cycle", default=default_cycle, type=float,
                        help="Delay to wait between 2 probes of each node, default ={}"
                        .format(default_cycle))
    parser.add_argument("-w", "--no-wlan", dest="report_wlan", default=True, action='store_true',
                        help="avoid probing of wlan traffic rates")
    parser.add_argument("-H", "--sidecar-hostname", dest="sidecar_hostname", default=None)
    parser.add_argument("-P", "--sidecar-port", dest="sidecar_port", default=None)
    parser.add_argument("-d", "--debug", dest="debug", action='store_true', default=False)
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args)
    loop = asyncio.get_event_loop()
    message_bus = asyncio.Queue()

    # xxx having to feed a Display instance with nodes
    # at creation time is a nuisance
    display = Display([], message_bus)

    from rhubarbe.logger import logger
    logger.info({'selected_nodes' : selector})
    monitor = Monitor(selector.cmc_names(),
                      message_bus = message_bus,
                      cycle = args.cycle,
                      report_wlan=args.report_wlan,
                      sidecar_hostname=args.sidecar_hostname,
                      sidecar_port=args.sidecar_port,
                      debug=args.debug)

    # trap signals so we get a nice message in monitor.log
    import signal
    import functools
    def exiting(signame):
        logger.info("Received signal {} - exiting".format(signame))
        loop.stop()
        exit(1)
    for signame in ('SIGHUP', 'SIGQUIT', 'SIGINT', 'SIGTERM'):
        loop.add_signal_handler(getattr(signal, signame),
                                functools.partial(exiting, signame))

    @asyncio.coroutine
    def run():
        # run both the core and the log loop in parallel
        yield from asyncio.gather(monitor.run(), monitor.log())
        yield from display.stop()

    t1 = util.self_manage(run())
    t2 = util.self_manage(display.run())
    tasks = asyncio.gather(t1, t2)
    wrapper = asyncio.gather(tasks)
    try:
        loop.run_until_complete(wrapper)
        return 0
    except KeyboardInterrupt as e:
        logger.info("rhubarbe-monitor : keyboard interrupt - exiting")
        tasks.cancel()
        loop.run_forever()
        tasks.exception()
        return 1
    except asyncio.TimeoutError as e:
        logger.info("rhubarbe-monitor : timeout expired after {}s".format(args.timeout))
        return 1
    finally:
        loop.close()
    

####################
@subcommand
def leases(*argv):
    usage = """
    Unless otherwise specified, displays current leases
    """
    parser = ArgumentParser(usage=usage)
    parser.add_argument('-c', '--check', action='store_true', default=False,
                        help="Check if you currently have a lease")
    parser.add_argument('-i', '--interactive', action='store_true', default=False,
                        help="Interactively prompt for commands (create, update, delete)")
    args = parser.parse_args(argv)
    from rhubarbe.leases import Leases
    message_bus = asyncio.Queue()
    leases = Leases(message_bus)
    loop = asyncio.get_event_loop()
    if args.check:
        @asyncio.coroutine
        def check_leases():
            ok = yield from leases.currently_valid()
            print("Access currently {}".format("granted" if ok else "denied"))
            return 0 if ok else 1
        return(loop.run_until_complete(check_leases()))
    else:
        loop.run_until_complete(leases.main(args.interactive))
        loop.close()
        return 0

####################
@subcommand
def images(*argv):
    usage = """
    Display available images
    """
    parser = ArgumentParser(usage=usage)
    parser.add_argument("-s", "--size", dest='sort_size', action='store_true', default=None,
                        help="sort by size (default)")
    parser.add_argument("-d", "--date", dest='sort_date', action='store_true', default=None,
                        help="sort by date")
    parser.add_argument("-r", "--reverse", action='store_true', default=False,
                        help="reverse sort")
    parser.add_argument("-v", "--verbose", action='store_true', default=False,
                        help="show all files, don't trim real files when they have a symlink")
    parser.add_argument("focus", nargs="*", type=str,
                        help="if provided, only images that contain one of these strings are displayed")
    the_imagesrepo = ImagesRepo()
    args = parser.parse_args(argv)
    if args.sort_size is not None:
        args.sort_by = 'size'
    elif args.sort_date is not None:
        args.sort_by = 'date'
    else:
        args.sort_by = 'size'
    # if focus is an empty list, then eveything is shown
    the_imagesrepo.display(args.focus, args.verbose, args.sort_by, args.reverse)
    return 0

####################
@subcommand
def inventory(*argv):
    usage = """
    Display inventory
    """
    parser = ArgumentParser(usage=usage)
    args = parser.parse_args(argv)
    the_inventory = Inventory()
    the_inventory.display(verbose=True)
    return 0

####################
@subcommand
def config(*argv):
    usage = """
    Display global configuration
    """
    parser = ArgumentParser(usage=usage)
    parser.add_argument("sections", nargs='*', 
                        type=str,
                        help="config section(s) to display")
    args = parser.parse_args(argv)
    the_config = Config()
    the_config.display(args.sections)
    return 0

####################
@subcommand
def version(*argv):
    from rhubarbe.version import version
    print("rhubarbe version {}".format(version))
    return 0
