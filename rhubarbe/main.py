#!/usr/bin/env python3

"""
Command-line entry point
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111,w1202,r1705


# DON'T import logger globally here
# we need to be able to mess inside the logger module
# before it gets loaded from another way

import asyncio

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter

from pkg_resources import resource_string

from asynciojobs import Scheduler, Job

import logging
from asyncssh.logging import set_log_level as asyncssh_set_log_level

from rhubarbe.config import Config
from rhubarbe.imagesrepo import ImagesRepo
from rhubarbe.selector import (Selector,
                               add_selector_arguments, selected_selector)
from rhubarbe.action import Action
from rhubarbe.display import Display
from rhubarbe.display_curses import DisplayCurses
from rhubarbe.node import Node
from rhubarbe.imageloader import ImageLoader
from rhubarbe.imagesaver import ImageSaver
from rhubarbe.monitor.nodes import MonitorNodes
from rhubarbe.monitor.loop import MonitorLoop
from rhubarbe.monitor.phones import MonitorPhones
from rhubarbe.monitor.leases import MonitorLeases
from rhubarbe.monitor.accountsmanager import AccountsManager
from rhubarbe.ssh import SshProxy
from rhubarbe.leases import Leases
from rhubarbe.inventory import Inventory
from rhubarbe.inventoryphones import InventoryPhones


# a supported command comes with a driver function
# in this module, that takes a list of args
# and returns 0 for success and s/t else otherwise
# specifically, command
# rhubarbe load -i fedora 12
# would result in a call
# load ( "-i", "fedora", "12" )


####################
RESERVATION_REQUIRED = "This function requires a valid reservation - "\
                       "or to be root or a privileged user"


####################
# exposed to the outside world (typically r2lab's nightly)

def check_reservation(leases, *,                        # pylint: disable=w0621
                      root_allowed=True, verbose=False, login=None):
    """
    return a bool indicating if a login currently has a lease

    if login is None, the current login is used instead

    when True, root_allowed means that the root user is always granted access

    verbose can be
    None  : does not write anything
    False : write a message if lease is not there
    True : always write a message
    """

    async def check_leases():
        # login = None means use my login
        # we don't use booked_now_by_me because of the verbose message
        # we use another variable to avoid using nonlocal
        actual_login = login or leases.login
        if verbose:
            print(f"Checking current reservation for {actual_login} : ", end="")
        is_fine = await leases.booked_now_by(login=actual_login,
                                             root_allowed=root_allowed)
        if is_fine:
            if verbose:
                print("OK")
        else:
            if verbose is None:
                pass
            elif verbose:
                print(f"WARNING: Access currently denied to {actual_login}")
            else:
                print("access denied")
        return is_fine
    return asyncio.get_event_loop().run_until_complete(check_leases())


def no_reservation(leases):                             # pylint: disable=w0621
    """
    returns True if nobody currently has a lease
    """
    async def check_leases():
        return not await leases.booked_now_by_anyone()
    return asyncio.get_event_loop().run_until_complete(check_leases())


####################
# NOTE: when adding a new command, please update setup.py as well
supported_subcommands = []                              # pylint: disable=c0103


def subcommand(driver):
    supported_subcommands.append(driver.__name__)
    return driver

####################


@subcommand
def nodes(*argv):
    usage = """
    Just display the hostnames of selected nodes
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    add_selector_arguments(parser)
    args = parser.parse_args(argv)
    selector = selected_selector(args)
    print(" ".join(selector.node_names()))

####################


def cmc_verb(verb, resa_policy, *argv):
    """
    resa_policy can be either
    (*) 'enforce': refuse to send the message if the lease is not there
    (*) 'warn': issue a warning when the lease is not there
    (*) 'none' - or anything else really: does not check the leases
    """
    usage = f"""
    Send verb '{verb}' to the CMC interface of selected nodes"""
    if resa_policy == 'enforce':
        usage += f"\n    {RESERVATION_REQUIRED}"
    config = Config()
    default_timeout = config.value('nodes', 'cmc_default_timeout')

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-t", "--timeout", action='store',
                        default=default_timeout, type=float,
                        help="Specify global timeout for the whole process")
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    message_bus = asyncio.Queue()
    leases = Leases(message_bus)                        # pylint: disable=w0621

    if resa_policy in ('warn', 'enforce'):
        reserved = check_reservation(leases, verbose=False)
        if not reserved:
            if resa_policy == 'enforce':
                return 1

    selector = selected_selector(args)
    action = Action(verb, selector)

    return 0 if action.run(message_bus, args.timeout) else 1

#####


@subcommand
def status(*argv):
    return cmc_verb('status', 'warn', *argv)


@subcommand
def on(*argv):                                          # pylint: disable=c0103
    return cmc_verb('on', 'enforce', *argv)


@subcommand
def off(*argv):
    return cmc_verb('off', 'enforce', *argv)


@subcommand
def reset(*argv):
    return cmc_verb('reset', 'enforce', *argv)


@subcommand
def info(*argv):
    return cmc_verb('info', 'warn', *argv)


@subcommand
def usrpstatus(*argv):
    return cmc_verb('usrpstatus', 'warn', *argv)


@subcommand
def usrpon(*argv):
    return cmc_verb('usrpon', 'enforce', *argv)


@subcommand
def usrpoff(*argv):
    return cmc_verb('usrpoff', 'enforce', *argv)


#####
# xxx should be asynchronous
@subcommand
def bye(*argv):
    """
    An alternative implementation of the previous 'all-off' utility
    Switch the lights off when you leave
    """

    usage = """
    Turn off whole testbed
    """
    config = Config()
    default_timeout = config.value('nodes', 'cmc_default_timeout')
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-t", "--timeout", action='store',
                        default=default_timeout, type=float,
                        help="Specify timeout for each phase")
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args, defaults_to_all=True)
    if selector.is_empty():
        selector.use_all_scope()

    bus = asyncio.Queue()
    Action('usrpoff', selector).run(bus, args.timeout)

    # keep it simple for now
    import time
    time.sleep(1)
    Action('off', selector).run(bus, args.timeout)

    # even simpler
    import os
    phones_inventory = InventoryPhones()
    for phone in phones_inventory.all_phones():
        command = (f"ssh -i {phone['gw_key']} "
                   f"{phone['gw_user']}@{phone['gw_host']} phone-off")
        print(command)
        os.system(command)

####################


@subcommand
def load(*argv):
    usage = f"""
    Load an image on selected nodes in parallel
    {RESERVATION_REQUIRED}
    """
    config = Config()
    config.check_binaries()
    imagesrepo = ImagesRepo()

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-i", "--image", action='store',
                        default=imagesrepo.default(),
                        help="Specify image to load")
    parser.add_argument("-t", "--timeout", action='store',
                        default=config.value('nodes',
                                                 'load_default_timeout'),
                        type=float,
                        help="Specify global timeout for the whole process")
    parser.add_argument("-b", "--bandwidth", action='store',
                        default=config.value('networking', 'bandwidth'),
                        type=int,
                        help="Set bandwidth in Mibps for frisbee uploading")
    parser.add_argument("-c", "--curses", action='store_true', default=False,
                        help="Use curses to provide term-based animation")
    # this is more for debugging
    parser.add_argument("-n", "--no-reset", dest='reset',
                        action='store_false', default=True,
                        help="""use this with nodes that are already
                        running a frisbee image. They won't get reset,
                        neither before or after the frisbee session""")
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    message_bus = asyncio.Queue()

    selector = selected_selector(args)
    if selector.is_empty():
        parser.print_help()
        return 1
    nodes = [Node(cmc_name, message_bus)                # pylint: disable=w0621
             for cmc_name in selector.cmc_names()]

    # send feedback
    message_bus.put_nowait({'selected_nodes': selector})
    from rhubarbe.logger import logger
    logger.info(f"timeout is {args.timeout}s")
    logger.info(f"bandwidth is {args.bandwidth} Mibps")

    actual_image = imagesrepo.locate_image(args.image, look_in_global=True)
    if not actual_image:
        print(f"Image file {args.image} not found - emergency exit")
        exit(1)

    # send feedback
    message_bus.put_nowait({'loading_image': actual_image})
    display_class = Display if not args.curses else DisplayCurses
    display = display_class(nodes, message_bus)
    loader = ImageLoader(nodes, image=actual_image, bandwidth=args.bandwidth,
                         message_bus=message_bus, display=display)
    return loader.main(reset=args.reset, timeout=args.timeout)

####################


@subcommand
def save(*argv):
    usage = f"""
    Save an image from a node
    Mandatory radical needs to be provided with --output
      This info, together with nodename and date, is stored
      on resulting image in /etc/rhubarbe-image
    {RESERVATION_REQUIRED}
    """

    config = Config()
    config.check_binaries()

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-o", "--output", action='store', dest='radical',
                        default=None, required=True,
                        help="Mandatory radical to name resulting image")
    parser.add_argument("-t", "--timeout", action='store',
                        default=config.value('nodes',
                                                 'save_default_timeout'),
                        type=float,
                        help="Specify global timeout for the whole process")
    parser.add_argument("-c", "--comment", dest='comment', default=None,
                        help="one-liner comment to insert in "
                        "/etc/rhubarbe-image")
    parser.add_argument("-n", "--no-reset", dest='reset',
                        action='store_false', default=True,
                        help="""use this with a node that is already
                        running a frisbee image. It won't get reset,
                        neither before or after the frisbee session""")
    parser.add_argument("node")
    args = parser.parse_args(argv)

    message_bus = asyncio.Queue()

    selector = Selector()
    selector.add_range(args.node)
    # in case there was one argument but it was not found in inventory
    if len(selector) != 1:
        parser.print_help()
    cmc_name = next(selector.cmc_names())
    node = Node(cmc_name, message_bus)
    nodename = node.control_hostname()

    imagesrepo = ImagesRepo()
    actual_image = imagesrepo.where_to_save(nodename, args.radical)
    message_bus.put_nowait({'info': f"Saving image {actual_image}"})
    # curses has no interest here since we focus on one node
    display_class = Display
    display = display_class([node], message_bus)
    saver = ImageSaver(node, image=actual_image, radical=args.radical,
                       message_bus=message_bus, display=display,
                       comment=args.comment)
    return saver.main(reset=args.reset, timeout=args.timeout)

####################


@subcommand
def wait(*argv):                                        # pylint: disable=r0914
    usage = """
    Wait for selected nodes to be reachable by ssh
    Returns 0 if all nodes indeed are reachable
    """
    # suppress info log messages from asyncssh
    asyncssh_set_log_level(logging.WARNING)

    config = Config()
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-c", "--curses", action='store_true', default=False,
                        help="Use curses to provide term-based animation")
    parser.add_argument("-t", "--timeout", action='store',
                        default=config.value('nodes',
                                                 'wait_default_timeout'),
                        type=float,
                        help="Specify global timeout for the whole process")
    parser.add_argument("-b", "--backoff", action='store',
                        default=config.value('networking', 'ssh_backoff'),
                        type=float,
                        help="Specify backoff average between "
                        "attempts to ssh connect")
    # really dont' write anything
    parser.add_argument("-s", "--silent", action='store_true', default=False)
    parser.add_argument("-v", "--verbose", action='store_true', default=False)

    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    # --curses implies --verbose otherwise nothing shows up
    if args.curses:
        args.verbose = True

    selector = selected_selector(args)
    message_bus = asyncio.Queue()

    if args.verbose:
        message_bus.put_nowait({'selected_nodes': selector})
    from rhubarbe.logger import logger
    logger.info(f"wait: backoff is {args.backoff} "
                f"and global timeout is {args.timeout}")

    nodes = [Node(cmc_name, message_bus)                # pylint: disable=w0621
             for cmc_name in selector.cmc_names()]
    sshs = [SshProxy(node, verbose=args.verbose) for node in nodes]
    jobs = [Job(ssh.wait_for(args.backoff), critical=True) for ssh in sshs]

    display_class = Display if not args.curses else DisplayCurses
    display = display_class(nodes, message_bus)

    # have the display class run forever until the other ones are done
    scheduler = Scheduler(Job(display.run(), forever=True, critical=True),
                          *jobs,
                          timeout=args.timeout,
                          critical=False)
    try:
        orchestration = scheduler.run()
        if orchestration:
            return 0
        else:
            if args.verbose:
                scheduler.debrief()
            return 1
    except KeyboardInterrupt:
        print("rhubarbe-wait : keyboard interrupt - exiting")
        # xxx
        return 1
    finally:
        display.epilogue()
        if not args.silent:
            for ssh in sshs:
                print(f"{ssh.node}:ssh {'OK' if ssh.status else 'KO'}")

####################


@subcommand
def images(*argv):
    usage = """
    Display available images
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-l", "--labeled-only", dest="labeled",
                        action='store_true', default=False,
                        help="""if specified, only images that have at least
                        one symlink to them are shown""")
    parser.add_argument("-p", "--public-only",
                        action="store_true", default=False,
                        help="displays only publicly visible images")
    parser.add_argument("-s", "--size", dest='sort_size',
                        action='store_true', default=None,
                        help="sort by size (default is by name)")
    parser.add_argument("-d", "--date", dest='sort_date',
                        action='store_true', default=None,
                        help="sort by date")
    parser.add_argument("-r", "--reverse",
                        action='store_true', default=False,
                        help="reverse sort")
    parser.add_argument("-n", "--narrow",
                        action='store_true', default=False,
                        help="""default is to show full paths, with this option
                        only radicals are displayed""")
    parser.add_argument("focus", nargs="*", type=str,
                        help="if provided, only images that contain "
                        "one of these strings are displayed")
    args = parser.parse_args(argv)
    imagesrepo = ImagesRepo()
    if args.sort_size is not None:
        args.sort_by = 'size'
    elif args.sort_date is not None:
        args.sort_by = 'date'
    else:
        args.sort_by = 'name'

    # if focus is an empty list, then everything is shown
    return imagesrepo.images(
        args.focus, args.sort_by, args.reverse,
        args.labeled, args.public_only, args.narrow)

####################


@subcommand
def resolve(*argv):
    usage = """for each input, find out and display
    what file exactly would be used if used with load
    and possible siblings if verbose mode is selected
    """

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
#    parser.add_argument("-r", "--reverse", action='store_true', default=False,
#                        help="reverse sort")
    parser.add_argument("-v", "--verbose", action='store_true', default=False,
                        help="show all files (i.e. with all symlinks)")
    parser.add_argument("focus", type=str,
                        help="the image radical name to resolve")
    args = parser.parse_args(argv)
    imagesrepo = ImagesRepo()
    # if focus is an empty list, then everything is shown
    return imagesrepo.resolve(args.focus, args.verbose)

####################


@subcommand
def share(*argv):
    usage = """
    Install privately-stored images into the global images repo
    Destination name is derived from the radical provided at save-time
      i.e. trailing part after saving__<date>__

    With the -a option, it is possible to make the new image
    **also** visible under that alias name.

    If your account is enabled in /etc/sudoers.d/rhubarbe-share,
    the command will actually perform the mv operation
    Requires to be run through 'sudo rhubarbe-share'
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-a", "--alias-name", dest='alias',
                        action='store', default=None,
                        help="also create a symlink of that name")
    # default=None so that imagesrepo.share can compute a default
    parser.add_argument("-n", "--dry-run",
                        default=None, action='store_true',
                        help="Only show what would be done "
                        "(default unless running under sudo")
    parser.add_argument("-f", "--force",
                        default=False, action='store_true',
                        help="Will move files even if destination exists")
    parser.add_argument("-c", "--clean",
                        default=False, action='store_true',
                        help="""Will remove other matches than the one that
                        gets promoted. In other words, useful when one name
                        has several matches and only the last one is desired.
                        Typically after a successful rsave""")
    parser.add_argument("image", type=str)
    args = parser.parse_args(argv)

    imagesrepo = ImagesRepo()
    return imagesrepo.share(
        args.image, args.alias, args.dry_run, args.force, args.clean)

####################


@subcommand
def leases(*argv):
    usage = """
    Unless otherwise specified, displays current leases, from today onwards
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument('-c', '--check', action='store_true', default=False,
                        help="Check if you currently have a lease")
    parser.add_argument('-i', '--interactive', action='store_true',
                        default=False,
                        help="Interactively prompt for commands "
                             "(create, update, delete)")
    args = parser.parse_args(argv)

    message_bus = asyncio.Queue()
    leases = Leases(message_bus)
    if args.check:
        access = check_reservation(leases, verbose=True)
        return 0 if access else 1
    else:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(leases.main(args.interactive))
        loop.close()
        return 0

####################


@subcommand
def monitornodes(*argv):                                # pylint: disable=r0914

    # xxx hacky - do a side effect in the logger module
    import rhubarbe.logger
    rhubarbe.logger.logger = rhubarbe.logger.monitor_logger
    from rhubarbe.logger import logger

    usage = """
    Cyclic probe all selected nodes, and reports
    real-time status at a sidecar service over websockets
    """
    config = Config()
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-c', "--cycle",
        default=config.value('monitor', 'cycle_nodes'),
        type=float,
        help="Delay to wait between 2 probes of each node")
    parser.add_argument(
        "-u", "--sidecar-url", dest="sidecar_url",
        default=Config().value('sidecar', 'url'),
        help="url for the sidecar server")
    parser.add_argument(
        "-w", "--wlan", dest="report_wlan",
        default=False, action='store_true',
        help="ask for probing of wlan traffic rates")
    parser.add_argument("-v", "--verbose",
                        action='store_true', default=False)
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args)
    message_bus = asyncio.Queue()

    # xxx having to feed a Display instance with nodes
    # at creation time is a nuisance
    display = Display([], message_bus)

    logger.info({'selected_nodes': selector})
    monitornodes = MonitorNodes(selector.cmc_names(),
                                message_bus=message_bus,
                                cycle=args.cycle,
                                sidecar_url=args.sidecar_url,
                                report_wlan=args.report_wlan,
                                verbose=args.verbose)

    async def async_main():
        # run both the core and the log loop in parallel
        await asyncio.gather(monitornodes.run_forever(),
                             display.run())

    MonitorLoop("monitornodes").run(async_main(), logger)
    return 0

####################


@subcommand
def monitorphones(*argv):

    # xxx hacky - do a side effect in the logger module
    import rhubarbe.logger
    rhubarbe.logger.logger = rhubarbe.logger.monitor_logger
    from rhubarbe.logger import logger

    usage = """
    Cyclic probe all known phones, and reports real-time status
    at a sidecar service over websockets
    """
    config = Config()
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "-c", "--cycle",
        default=config.value('monitor', 'cycle_phones'),
        type=float,
        help="Delay to wait between 2 probes of each phone")
    parser.add_argument(
        "-u", "--sidecar-url", dest="sidecar_url",
        default=Config().value('sidecar', 'url'),
        help="url for the sidecar server")
    parser.add_argument(
        "-v", "--verbose", action='store_true')
    args = parser.parse_args(argv)

    logger.info("Using all phones")
    monitorphones = MonitorPhones(**vars(args))

    MonitorLoop("monitorphones").run(
        monitorphones.run_forever(),
        logger)
    return 0

####################


@subcommand
def monitorleases(*argv):

    # xxx hacky - do a side effect in the logger module
    import rhubarbe.logger
    rhubarbe.logger.logger = rhubarbe.logger.monitor_logger
    from rhubarbe.logger import logger

    usage="""
    Cyclic check of leases; also reacts to 'request' messages on
    the sidecar channel, which triggers leases acquisition right away.
    See config for defaults.
    """
    config = Config()
    parser = ArgumentParser(
        usage=usage, formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "-u", "--sidecar-url", dest="sidecar_url",
        default=Config().value('sidecar', 'url'),
        help="url for the sidecar server")
    parser.add_argument(
        "-v", "--verbose", default=False, action='store_true')
    args = parser.parse_args(argv)

    message_bus = asyncio.Queue()


    monitorleases = MonitorLeases(
        message_bus, args.sidecar_url, args.verbose)

    MonitorLoop("monitorleases").run(
        monitorleases.run_forever(),
        logger)
    return 0

####################


@subcommand
def accountsmanager(*argv):

    usage = "The core of the accounts manager; reserved to root"

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-c", "--cycle",
                        help="Set cycle in seconds; 0 means run only once;"
                             " default from config file.",
                        default=None)
    args = parser.parse_args(argv)

    accounts_manager = AccountsManager()
    return accounts_manager.main(args.cycle)

####################


@subcommand
def inventory(*argv):
    usage = """
    Display inventory
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.parse_args(argv)
    inventory = Inventory()
    inventory.display(verbose=True)
    return 0

####################


@subcommand
def config(*argv):
    usage = """
    Display global configuration
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("sections", nargs='*',
                        type=str,
                        help="config section(s) to display")
    args = parser.parse_args(argv)
    config = Config()
    config.display(args.sections)
    return 0

####################


@subcommand
def template(*argv):
    usage = """
    Show the template file to create the nodes and phones inventory
    under /etc/rhubarbe
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "-n", "--nodes", dest='nodes',
        action='store_true', default=False,
        help="Show template for /etc/rhubarbe/inventory-nodes.json")
    parser.add_argument(
        "-p", "--phones", dest='phones',
        action='store_true', default=False,
        help="Show template for /etc/rhubarbe/inventory-phones.json")
    args = parser.parse_args(argv)

    def show_template(nodes_or_phones):
        template = resource_string(
            'rhubarbe', f"config/inventory-{nodes_or_phones}.json.template")
        print("# =========="
              f" template for /etc/rhubarbe/inventory-{nodes_or_phones}.json")
        print(template.decode(encoding='utf-8'))

    # pick -n by default
    if not args.nodes and not args.phones:
        args.nodes = True

    for nodes_or_phones in ('nodes', 'phones'):
        selected = getattr(args, nodes_or_phones)
        if selected:
            show_template(nodes_or_phones)

####################


@subcommand
def version(*_):
    from rhubarbe.version import __version__
    print(f"rhubarbe version {__version__}")
    return 0
