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
from rhubarbe.monitor import Monitor
from rhubarbe.monitorphones import MonitorPhones
from rhubarbe.accounts import Accounts
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
            print("Checking current reservation for {} : "
                  .format(actual_login), end="")
        is_fine = await leases.booked_now_by(login=actual_login,
                                             root_allowed=root_allowed)
        if is_fine:
            if verbose:
                print("OK")
        else:
            if verbose is None:
                pass
            elif verbose:
                print("WARNING: Access currently denied to {}"
                      .format(actual_login))
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
    usage = """
    Send verb '{verb}' to the CMC interface of selected nodes"""\
    .format(verb=verb)
    if resa_policy == 'enforce':
        usage += "\n    {policy}".format(policy=RESERVATION_REQUIRED)
    the_config = Config()
    default_timeout = the_config.value('nodes', 'cmc_default_timeout')

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
    the_config = Config()
    default_timeout = the_config.value('nodes', 'cmc_default_timeout')
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
        command = "ssh -i {key} {user}@{gateway} phone-off"\
                  .format(key=phone['gw_key'],
                          user=phone['gw_user'],
                          gateway=phone['gw_host'])
        print(command)
        os.system(command)

####################


@subcommand
def load(*argv):
    usage = """
    Load an image on selected nodes in parallel
    {resa}
    """.format(resa=RESERVATION_REQUIRED)
    the_config = Config()
    the_config.check_binaries()
    the_imagesrepo = ImagesRepo()

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-i", "--image", action='store',
                        default=the_imagesrepo.default(),
                        help="Specify image to load")
    parser.add_argument("-t", "--timeout", action='store',
                        default=the_config.value('nodes',
                                                 'load_default_timeout'),
                        type=float,
                        help="Specify global timeout for the whole process")
    parser.add_argument("-b", "--bandwidth", action='store',
                        default=the_config.value('networking', 'bandwidth'),
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
    logger.info("timeout is {}s".format(args.timeout))
    logger.info("bandwidth is {} Mibps".format(args.bandwidth))

    actual_image = the_imagesrepo.locate_image(args.image, look_in_global=True)
    if not actual_image:
        print("Image file {} not found - emergency exit".format(args.image))
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
    usage = """
    Save an image from a node
    Mandatory radical needs to be provided with --output
      This info, together with nodename and date, is stored
      on resulting image in /etc/rhubarbe-image
    {resa}
    """.format(resa=RESERVATION_REQUIRED)

    the_config = Config()
    the_config.check_binaries()

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-o", "--output", action='store', dest='radical',
                        default=None, required=True,
                        help="Mandatory radical to name resulting image")
    parser.add_argument("-t", "--timeout", action='store',
                        default=the_config.value('nodes',
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

    the_imagesrepo = ImagesRepo()
    actual_image = the_imagesrepo.where_to_save(nodename, args.radical)
    message_bus.put_nowait({'info': "Saving image {}".format(actual_image)})
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
    the_config = Config()
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-c", "--curses", action='store_true', default=False,
                        help="Use curses to provide term-based animation")
    parser.add_argument("-t", "--timeout", action='store',
                        default=the_config.value('nodes',
                                                 'wait_default_timeout'),
                        type=float,
                        help="Specify global timeout for the whole process")
    parser.add_argument("-b", "--backoff", action='store',
                        default=the_config.value('networking', 'ssh_backoff'),
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
    logger.info("wait: backoff is {} and global timeout is {}"
                .format(args.backoff, args.timeout))

    nodes = [Node(cmc_name, message_bus)                # pylint: disable=w0621
             for cmc_name in selector.cmc_names()]
    sshs = [SshProxy(node, verbose=args.verbose) for node in nodes]
    jobs = [Job(ssh.wait_for(args.backoff), critical=True) for ssh in sshs]

    display_class = Display if not args.curses else DisplayCurses
    display = display_class(nodes, message_bus)

    # have the display class run forever until the other ones are done
    scheduler = Scheduler(
        Job(display.run(), forever=True, critical=True),
        *jobs, timeout=args.timeout)
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
                print("{}:ssh {}".format(ssh.node,
                                         "OK" if ssh.status else "KO"))

####################


@subcommand
def images(*argv):
    usage = """
    Display available images
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-s", "--size", dest='sort_size',
                        action='store_true', default=None,
                        help="sort by size (default)")
    parser.add_argument("-d", "--date", dest='sort_date',
                        action='store_true', default=None,
                        help="sort by date")
    parser.add_argument("-r", "--reverse",
                        action='store_true', default=False,
                        help="reverse sort")
    parser.add_argument("-v", "--verbose",
                        action='store_true', default=False,
                        help="show all files, including the ones "
                        "that do not have a symlink/alias")
    parser.add_argument("focus", nargs="*", type=str,
                        help="if provided, only images that contain "
                        "one of these strings are displayed")
    args = parser.parse_args(argv)
    the_imagesrepo = ImagesRepo()
    if args.sort_size is not None:
        args.sort_by = 'size'
    elif args.sort_date is not None:
        args.sort_by = 'date'
    else:
        args.sort_by = 'size'
    # if focus is an empty list, then everything is shown
    the_imagesrepo.main(args.focus, args.verbose, args.sort_by, args.reverse)
    return 0

####################


@subcommand
def resolve(*argv):
    usage = """
    For each input, find out any display
    what file exactly would be used if used with load
    and possible siblings if verbose mode is selected
    """

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-r", "--reverse", action='store_true', default=False,
                        help="reverse sort")
    parser.add_argument("-v", "--verbose", action='store_true', default=False,
                        help="show all files (i.e. with all symlinks)")
    parser.add_argument("focus", nargs="*", type=str,
                        help="the names to resolve")
    args = parser.parse_args(argv)
    the_imagesrepo = ImagesRepo()
    # if focus is an empty list, then everything is shown
    the_imagesrepo.resolve(args.focus, args.verbose, args.reverse)
    return 0

####################


@subcommand
def share(*argv):
    usage = """
    Install privately-stored images into the global images repo
    Destination name is derived from the radical provided at save-time
      i.e. trailing part after saving__<date>__
    When only one image is provided it is possible to specify
      another destination name with -o

    If your account is enabled in /etc/sudoers.d/rhubarbe-share,
    the command will actually perform the mv operation
    Requires to be run through 'sudo rhubarbe-share'
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-a", "--alias-name", dest='alias',
                        action='store', default=None,
                        help="create a symlink of that name "
                        "(ignored with more than one image)")
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
                        Typically after a successful (re)save""")
    parser.add_argument("images", nargs="+", type=str)
    args = parser.parse_args(argv)

    the_imagesrepo = ImagesRepo()
    return the_imagesrepo.share(args.images, args.alias,
                                args.dry_run, args.force, args.clean)


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
    the_leases = Leases(message_bus)
    if args.check:
        access = check_reservation(the_leases, verbose=True)
        return 0 if access else 1
    else:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(the_leases.main(args.interactive))
        loop.close()
        return 0

####################


@subcommand
def monitor(*argv):                                     # pylint: disable=r0914

    # xxx hacky - do a side effect in the logger module
    import rhubarbe.logger
    rhubarbe.logger.logger = rhubarbe.logger.monitor_logger
    # xxx hacky

    usage = """
    Cyclic probe all selected nodes to report real-time status
    at a sidecar service over socketIO
    """
    the_config = Config()
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument('-c', "--cycle",
                        default=the_config.value('monitor', 'cycle_nodes'),
                        type=float,
                        help="Delay to wait between 2 probes of each node")
    parser.add_argument("-u", "--sidecar-url", dest="sidecar_url",
                        default=Config().value('sidecar', 'url'),
                        help="url for thesidecar server")
    parser.add_argument("-w", "--wlan", dest="report_wlan",
                        default=False, action='store_true',
                        help="ask for probing of wlan traffic rates")
    parser.add_argument("-v", "--verbose",
                        action='store_true', default=False)
    add_selector_arguments(parser)
    args = parser.parse_args(argv)

    selector = selected_selector(args)
    loop = asyncio.get_event_loop()
    message_bus = asyncio.Queue()

    # xxx having to feed a Display instance with nodes
    # at creation time is a nuisance
    display = Display([], message_bus)

    from rhubarbe.logger import logger
    logger.info({'selected_nodes': selector})
    the_monitor = Monitor(selector.cmc_names(),
                          message_bus=message_bus,
                          cycle=args.cycle,
                          sidecar_url=args.sidecar_url,
                          report_wlan=args.report_wlan,
                          verbose=args.verbose)

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

    async def run():
        # run both the core and the log loop in parallel
        await asyncio.gather(the_monitor.run(), the_monitor.log(),
                             display.run())

    try:
        task = asyncio.ensure_future(run())
        loop.run_until_complete(task)
        return 0
    except KeyboardInterrupt:
        logger.info("rhubarbe-monitor : keyboard interrupt - exiting")
        task.cancel()
        loop.run_forever()
        task.exception()
        return 1
    except asyncio.TimeoutError:
        logger.info("rhubarbe-monitor : asyncio timeout expired")
        return 1
    finally:
        loop.close()

####################


@subcommand
def monitorphones(*argv):

    # xxx hacky - do a side effect in the logger module
    import rhubarbe.logger
    rhubarbe.logger.logger = rhubarbe.logger.monitor_logger
    # xxx hacky

    usage = """
    Cyclic probe all known phones to report real-time status
    at a sidecar service over socketIO
    """
    the_config = Config()
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument('-c', "--cycle",
                        default=the_config.value('monitor', 'cycle_phones'),
                        type=float,
                        help="Delay to wait between 2 probes of each phone")
    parser.add_argument("-u", "--sidecar-url", dest="sidecar_url",
                        default=Config().value('sidecar', 'url'),
                        help="url for the sidecar server")
    parser.add_argument("-v", "--verbose", action='store_true')
    args = parser.parse_args(argv)

    from rhubarbe.logger import logger
    logger.info("Using all phones")
    loop = asyncio.get_event_loop()
    the_monitorphones = MonitorPhones(**vars(args))

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

    try:
        task = asyncio.ensure_future(the_monitorphones.run())
        loop.run_until_complete(task)
        return 0
    except KeyboardInterrupt:
        logger.info("rhubarbe-monitorphones : keyboard interrupt - exiting")
        task.cancel()
        loop.run_forever()
        task.exception()
        return 1
    except asyncio.TimeoutError:
        logger.info("rhubarbe-monitor : asyncio timeout expired")
        return 1
    finally:
        loop.close()

####################


@subcommand
def accounts(*argv):

    usage = "The core of the accounts manager; reserved to root"

    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.add_argument("-c", "--cycle",
                        help="Set cycle in seconds; 0 means run only once;"
                             " default from config file.",
                        default=None)
    args = parser.parse_args(argv)

    the_accounts = Accounts()
    return the_accounts.main(args.cycle)

####################


@subcommand
def inventory(*argv):
    usage = """
    Display inventory
    """
    parser = ArgumentParser(usage=usage,
                            formatter_class=ArgumentDefaultsHelpFormatter)
    parser.parse_args(argv)
    the_inventory = Inventory()
    the_inventory.display(verbose=True)
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
    the_config = Config()
    the_config.display(args.sections)
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
        template = resource_string('rhubarbe',
                                   "config/inventory-{}.json.template"
                                   .format(nodes_or_phones))
        print("# =========="
              " template for /etc/rhubarbe/inventory-{}.json"
              .format(nodes_or_phones))
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
    print("rhubarbe version {}".format(__version__))
    return 0
