#!/usr/bin/env python3

"""
Command-line entry point — all subcommands are click commands registered
on the top-level `cli` group.
"""

# pylint: disable=missing-function-docstring, logging-fstring-interpolation
# pylint: disable=redefined-outer-name, import-outside-toplevel, fixme

# DON'T import logger globally here
# we need to be able to mess inside the logger module
# before it gets loaded from another way

import os
import sys
import time
import logging
import asyncio
import traceback
from importlib import resources

import click
import requests

from asynciojobs import Scheduler, Job
from asyncssh.logging import set_log_level as asyncssh_set_log_level

from .config import Config
from .imagesrepo import ImagesRepo
from .selector import Selector, MisformedRange
from .action import Action
from .display import Display
from .display_curses import DisplayCurses
from .node import Node
from .imageloader import ImageLoader
from .imagesaver import ImageSaver
from .monitor.loop import MonitorLoop
from .monitor.nodes import MonitorNodes
from .monitor.phones import MonitorPhones
from .monitor.pdus import MonitorPdus
from .monitor.prometheus import PrometheusExporter
from .monitor.accountsmanager import AccountsManager
from .ssh import SshProxy
from .leases import Leases
from .book import Book
from .inventorynodes import InventoryNodes
from .inventoryphones import InventoryPhones
from .inventorypdus import InventoryPdus
from .inventoryrelays import InventoryRelays

from .logger import monitor_logger


# ssh.py logs its debug messages in monitor_logger
# we want that off in interactive commands
def turn_off_ssh_debug_messages():
    class NoDebugFilter(logging.Filter):
        def filter(self, record):
            return record.levelno != logging.DEBUG
    monitor_logger.addFilter(NoDebugFilter())


RESERVATION_REQUIRED = ("This function requires a valid reservation - "
                        "or to be root or a privileged user")


####################
# exposed to the outside world (typically r2lab's nightly)

def check_reservation(leases, *,
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
    return asyncio.new_event_loop().run_until_complete(check_leases())


def no_reservation(leases):
    """
    returns True if nobody currently has a lease
    """
    async def check_leases():
        return not await leases.booked_now_by_anyone()
    return asyncio.new_event_loop().run_until_complete(check_leases())


####################
# shared selector handling (replaces add_selector_arguments / selected_selector)

_SELECTOR_RANGES_HELP = """\
nodes can be specified one by one, like: 1 004 fit01 reboot01;
or by range (inclusive) like: 2-12, fit4-reboot12;
or by range+step like 4-16-2 (all even numbers from 4 to 16);
ranges can also be excluded with '~', so ~1-4 means remove 1,2,3,4.

ex: 1-4 7-25-2 ~11-19-4  yields  1 2 3 4 7 9 13 17 21 23 25
"""

_ALL_NODES_HELP = (
    "add the contents of the testbed.all_scope config variable in the mix; "
    "can be combined with ranges, e.g. -a ~4-16-2")


def selector_options(f):
    """Decorator that adds -a/--all-nodes and a variadic RANGES argument."""
    f = click.argument('ranges', nargs=-1)(f)
    f = click.option('-a', '--all-nodes', is_flag=True, default=False,
                     help=_ALL_NODES_HELP)(f)
    return f


def build_selector(ranges, all_nodes, *, defaults_to_all=False):
    selector = Selector()
    if all_nodes:
        selector.use_all_scope()
    if not ranges and not all_nodes:
        if defaults_to_all:
            selector.use_all_scope()
        elif os.getenv('NODES'):
            for node in os.environ['NODES'].split():
                selector.add_range(node)
        else:
            raise click.UsageError(
                "no argument specified: "
                "this requires you to set env. variable 'NODES'")
    else:
        for one_range in ranges:
            selector.add_range(one_range)
    return selector


####################
# top-level group

CONTEXT_SETTINGS = {
    'help_option_names': ['-h', '--help'],
    'max_content_width': 100,
}


class AliasedGroup(click.Group):
    """A click.Group that accepts unambiguous prefixes of subcommand names,
    plus an optional dict of exact-name aliases.

    Exact (canonical or alias) matches always win; an ambiguous prefix errors
    with the candidates. Aliases are not surfaced in --help and don't take
    part in prefix matching.
    """

    def __init__(self, *args, aliases=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.aliases = aliases or {}

    def get_command(self, ctx, cmd_name):
        exact = super().get_command(ctx, cmd_name)
        if exact is not None:
            return exact
        if cmd_name in self.aliases:
            return super().get_command(ctx, self.aliases[cmd_name])
        matches = [name for name in self.list_commands(ctx)
                   if name.startswith(cmd_name)]
        if not matches:
            return None
        if len(matches) == 1:
            return super().get_command(ctx, matches[0])
        ctx.fail(f"Ambiguous command '{cmd_name}': "
                 f"matches {', '.join(sorted(matches))}")
        return None


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
def cli():
    """rhubarbe — manage the R2Lab testbed."""


####################


@cli.command()
@selector_options
def nodes(all_nodes, ranges):
    """Just display the hostnames of selected nodes."""
    selector = build_selector(ranges, all_nodes)
    print(" ".join(selector.node_names()))


####################
# CMC verbs

def cmc_verb(verb, resa_policy, *, timeout, selector):
    """
    resa_policy can be either
    (*) 'enforce': refuse to send the message if the lease is not there
    (*) 'warn': issue a warning when the lease is not there
    (*) 'none' - or anything else really: does not check the leases
    """
    message_bus = asyncio.Queue()
    leases = Leases(message_bus)

    if resa_policy in ('warn', 'enforce'):
        reserved = check_reservation(leases, verbose=False)
        if not reserved and resa_policy == 'enforce':
            return 1

    action = Action(verb, selector)
    return 0 if action.run(message_bus, timeout) else 1


def _timeout_option(config_key='cmc_default_timeout'):
    return click.option(
        '-t', '--timeout', type=float, show_default=True,
        default=lambda: Config().value('nodes', config_key),
        help="Specify global timeout for the whole process")


def _cmc_command(verb, resa_policy, help_text):
    """Build a click command for a CMC verb."""
    def callback(timeout, all_nodes, ranges):
        selector = build_selector(ranges, all_nodes)
        sys.exit(cmc_verb(verb, resa_policy, timeout=timeout, selector=selector))
    callback.__name__ = verb
    callback.__doc__ = help_text
    callback = selector_options(callback)
    callback = _timeout_option()(callback)
    return cli.command(name=verb)(callback)


_cmc_command('status', 'warn',
             "Send 'status' to the CMC interface of selected nodes.")
_cmc_command('on', 'enforce',
             "Send 'on' to the CMC interface of selected nodes. "
             + RESERVATION_REQUIRED)
_cmc_command('off', 'enforce',
             "Send 'off' to the CMC interface of selected nodes. "
             + RESERVATION_REQUIRED)
_cmc_command('reset', 'enforce',
             "Send 'reset' to the CMC interface of selected nodes. "
             + RESERVATION_REQUIRED)
_cmc_command('info', 'warn',
             "Send 'info' to the CMC interface of selected nodes.")
_cmc_command('usrpstatus', 'warn',
             "Send 'usrpstatus' to the CMC interface of selected nodes.")
_cmc_command('usrpon', 'enforce',
             "Send 'usrpon' to the CMC interface of selected nodes. "
             + RESERVATION_REQUIRED)
_cmc_command('usrpoff', 'enforce',
             "Send 'usrpoff' to the CMC interface of selected nodes. "
             + RESERVATION_REQUIRED)
_cmc_command('bothoff', 'enforce',
             "Send 'bothoff' to the CMC interface of selected nodes. "
             + RESERVATION_REQUIRED)


####################


@cli.command()
@_timeout_option('cmc_safe_timeout')
@selector_options
def bye(timeout, all_nodes, ranges):
    """Turn off whole testbed (nodes, phones, auto-off PDUs)."""
    try:
        selector = build_selector(ranges, all_nodes, defaults_to_all=True)
        if selector.is_empty():
            selector.use_all_scope()

        message_bus = asyncio.Queue()
        print(f"{20*'='} bothoff {20*'='} (timeout={timeout})")
        Action('bothoff', selector).run(message_bus, timeout)

        # keep it simple for now
        time.sleep(1)

        print(f"{20*'='} phones {20*'='}")
        inventory_phones = InventoryPhones()
        for phone in inventory_phones.all_phones():
            command = (f"ssh -i {phone['gw_key']} -o StrictHostKeyChecking=no "
                       f"{phone['gw_user']}@{phone['gw_host']} phone-off")
            print(command)
            os.system(command)

        print(f"{20*'='} pdus {20*'='}")
        inventory_pdus = InventoryPdus.load()
        # sequential on purpose: the ssh service in the PDU unit looks fragile
        for device in inventory_pdus.devices:
            if device.auto_turn_off:
                asyncio.run(device.off())
    except KeyboardInterrupt:
        print("bye: keyboard interrupt - exiting")
        sys.exit(1)


####################


@cli.command()
@click.option('-i', '--image', default=lambda: ImagesRepo().default(),
              show_default=True, help="Specify image to load")
@_timeout_option('load_default_timeout')
@click.option('-b', '--bandwidth', type=int, show_default=True,
              default=lambda: Config().value('networking', 'bandwidth'),
              help="Set bandwidth in Mibps for frisbee uploading")
@click.option('-c', '--curses', is_flag=True, default=False,
              help="Use curses to provide term-based animation")
@click.option('-n', '--no-reset', 'no_reset', is_flag=True, default=False,
              help="use this with nodes that are already running a frisbee "
                   "image; they won't get reset, neither before or after "
                   "the frisbee session")
@selector_options
def load(image, timeout, bandwidth, curses, no_reset, all_nodes, ranges):
    """Load an image on selected nodes in parallel.

    \b
    This function requires a valid reservation
    - or to be root or a privileged user.
    """
    reset = not no_reset
    Config().check_binaries()
    imagesrepo = ImagesRepo()

    message_bus = asyncio.Queue()
    selector = build_selector(ranges, all_nodes)
    if selector.is_empty():
        raise click.UsageError("no nodes selected")
    node_list = [Node(cmc_name, message_bus)
                 for cmc_name in selector.cmc_names()]

    message_bus.put_nowait({'selected_nodes': selector})
    from rhubarbe.logger import logger
    logger.info(f"timeout is {timeout}s")
    logger.info(f"bandwidth is {bandwidth} Mibps")

    actual_image = imagesrepo.locate_image(image, look_in_global=True)
    if not actual_image:
        print(f"Image file {image} not found - emergency exit")
        sys.exit(1)

    message_bus.put_nowait({'loading_image': actual_image})
    display_class = DisplayCurses if curses else Display
    display = display_class(node_list, message_bus)
    loader = ImageLoader(node_list, image=actual_image, bandwidth=bandwidth,
                         message_bus=message_bus, display=display)
    sys.exit(loader.main(reset=reset, timeout=timeout))


####################


@cli.command()
@click.option('-o', '--output', 'radical', required=True,
              help="Mandatory radical to name resulting image")
@_timeout_option('save_default_timeout')
@click.option('-c', '--comment', default=None,
              help="one-liner comment to insert in /etc/rhubarbe-image")
@click.option('-n', '--no-reset', 'no_reset', is_flag=True, default=False,
              help="use this with a node that is already running a frisbee "
                   "image; it won't get reset, neither before or after "
                   "the frisbee session")
@click.argument('node')
def save(radical, timeout, comment, no_reset, node):
    """Save an image from a node.

    The mandatory radical (-o) — together with nodename and date —
    is stored on the resulting image in /etc/rhubarbe-image.
    """
    reset = not no_reset
    Config().check_binaries()

    message_bus = asyncio.Queue()
    selector = Selector()
    selector.add_range(node)
    if len(selector) != 1:
        raise click.UsageError(f"could not resolve node '{node}'")
    cmc_name = next(selector.cmc_names())
    node_obj = Node(cmc_name, message_bus)
    nodename = node_obj.control_hostname()

    imagesrepo = ImagesRepo()
    actual_image = imagesrepo.where_to_save(nodename, radical)
    message_bus.put_nowait({'info': f"Saving image {actual_image}"})
    display = Display([node_obj], message_bus)
    saver = ImageSaver(node_obj, image=actual_image, radical=radical,
                       message_bus=message_bus, display=display,
                       comment=comment)
    sys.exit(saver.main(reset=reset, timeout=timeout))


####################


@cli.command()
@click.option('-c', '--curses', is_flag=True, default=False,
              help="Use curses to provide term-based animation")
@_timeout_option('wait_default_timeout')
@click.option('-b', '--backoff', type=float, show_default=True,
              default=lambda: Config().value('networking', 'ssh_backoff'),
              help="Specify backoff average between attempts to ssh connect")
@click.option('-u', '--user', default="root", show_default=True,
              help="select other username")
@click.option('-s', '--silent', is_flag=True, default=False,
              help="really don't write anything")
@click.option('-v', '--verbose', is_flag=True, default=False)
@selector_options
def wait(curses, timeout, backoff, user, silent, verbose, all_nodes, ranges):
    """Wait for selected nodes to be reachable by ssh.

    Returns 0 if all nodes indeed are reachable.
    """
    asyncssh_set_log_level(logging.WARNING)
    turn_off_ssh_debug_messages()

    if curses:
        verbose = True

    selector = build_selector(ranges, all_nodes)
    message_bus = asyncio.Queue()

    if verbose:
        message_bus.put_nowait({'selected_nodes': selector})
    from rhubarbe.logger import logger
    logger.info(f"wait: backoff is {backoff} and global timeout is {timeout}")

    node_list = [Node(cmc_name, message_bus)
                 for cmc_name in selector.cmc_names()]
    sshs = [SshProxy(node, username=user, verbose=verbose)
            for node in node_list]
    jobs = [Job(ssh.wait_for(backoff), critical=True) for ssh in sshs]

    display_class = DisplayCurses if curses else Display
    display = display_class(node_list, message_bus)

    scheduler = Scheduler(Job(display.run(), forever=True, critical=True),
                          *jobs, timeout=timeout, critical=False)
    rc = 0
    try:
        orchestration = scheduler.run()
        if not orchestration:
            if verbose:
                scheduler.debrief(silence_done_jobs=True)
            rc = 1
    except KeyboardInterrupt:
        print("rhubarbe-wait : keyboard interrupt - exiting")
        rc = 1
    finally:
        display.epilogue()
        if not silent:
            for ssh in sshs:
                print(f"{ssh.node}:ssh {'OK' if ssh.status else 'KO'}")
    sys.exit(rc)


####################


@cli.command()
@click.option('-l', '--labeled-only', 'labeled', is_flag=True, default=False,
              help="only images that have at least one symlink to them")
@click.option('-p', '--public-only', is_flag=True, default=False,
              help="display only publicly visible images")
@click.option('-s', '--size', 'sort_size', is_flag=True, default=False,
              help="sort by size (default is by name)")
@click.option('-d', '--date', 'sort_date', is_flag=True, default=False,
              help="sort by date")
@click.option('-r', '--reverse', is_flag=True, default=False,
              help="reverse sort")
@click.option('-n', '--narrow', is_flag=True, default=False,
              help="show only radicals instead of full paths")
@click.option('-a', '--and', 'and_or', flag_value='and', default='or',
              help="use AND logic instead of OR")
@click.argument('focus', nargs=-1)
def images(labeled, public_only, sort_size, sort_date, reverse,
           narrow, and_or, focus):
    """Display available images."""
    imagesrepo = ImagesRepo()
    if sort_size:
        sort_by = 'size'
    elif sort_date:
        sort_by = 'date'
    else:
        sort_by = 'name'
    sys.exit(imagesrepo.images(
        list(focus), sort_by, reverse, labeled, public_only, narrow, and_or))


####################


@cli.command()
@click.option('-v', '--verbose', is_flag=True, default=False,
              help="show all files (i.e. with all symlinks)")
@click.argument('focus')
def resolve(verbose, focus):
    """For the input image, show what file would be used by `load`."""
    imagesrepo = ImagesRepo()
    sys.exit(imagesrepo.resolve(focus, verbose))


####################


@cli.command()
@click.option('-a', '--alias-name', 'alias', default=None,
              help="also create a symlink of that name")
@click.option('-n', '--dry-run', default=None, is_flag=True,
              help="only show what would be done "
                   "(default unless running under sudo)")
@click.option('-f', '--force', is_flag=True, default=False,
              help="move files even if destination exists")
@click.option('-c', '--clean', is_flag=True, default=False,
              help="remove other matches than the one being promoted; "
                   "useful when one name has several matches and only "
                   "the last one is desired (typically after a successful rsave)")
@click.argument('image')
def share(alias, dry_run, force, clean, image):
    """Install privately-stored images into the global images repo.

    Destination name is derived from the radical provided at save-time
    (the trailing part after `saving__<date>__`).

    With -a it is possible to make the new image also visible under that
    alias name. If your account is enabled in /etc/sudoers.d/rhubarbe-share,
    the command will actually perform the mv operation — requires running
    via `sudo rhubarbe-share`.
    """
    imagesrepo = ImagesRepo()
    sys.exit(imagesrepo.share(image, alias, dry_run, force, clean))


####################


TIME_HELP = "ISO timestamp, or keyword: now, today, tomorrow"


@cli.command()
@click.option('-c', '--check', is_flag=True,
              help="Check if you currently have a lease.")
@click.option('-a', '--after', default='now', show_default=True,
              help=f"Show leases ending after this time ({TIME_HELP}).")
@click.option('-b', '--before', default='tomorrow', show_default=True,
              help=f"Show leases starting before this time ({TIME_HELP}).")
@click.option('-u', '--upcoming', is_flag=True,
              help="Show all upcoming leases (ignore --before).")
@click.option('-n', '--no-admin', is_flag=True,
              help="Hide admin slices (r2lab-*).")
def leases(check, after, before, upcoming, no_admin):
    """Display leases; by default shows from now until midnight."""
    message_bus = asyncio.Queue()
    the_leases = Leases(message_bus)
    if check:
        access = check_reservation(the_leases, verbose=True)
        sys.exit(0 if access else 1)
    the_leases.after_filter = Leases.resolve_time_keyword(after)
    if upcoming:
        the_leases.before_filter = None
    else:
        the_leases.before_filter = Leases.resolve_time_keyword(before)
    the_leases.no_admin = no_admin
    loop = asyncio.new_event_loop()
    loop.run_until_complete(the_leases.main())
    loop.close()


####################


@cli.command()
@click.argument('start')
@click.argument('end')
@click.option('-q', '--query', is_flag=True,
              help="Don't book, just check if the slot is free.")
@click.option('-d', '--delete', is_flag=True,
              help="Delete the lease for the given time slot.")
@click.option('-s', '--slice', 'slice_name',
              help="Slice name (mandatory unless using -q or -d).")
@click.option('-e', '--email', help="Email for authentication.")
@click.option('-p', '--password', help="Password for authentication.")
@click.option('-v', '--verbose', is_flag=True, help="Be verbose.")
def book(start, end, query, delete, slice_name, email, password, verbose):
    """Book, query, or delete a lease on the testbed.

    START and END are time specs: 2020-01-01T00:00, "2020-01-01 00:00",
    or 14:00 (meaning today).
    """
    book_obj = Book(email=email, password=password, verbose=verbose)
    start_epoch = book_obj.canonical_date(start)
    end_epoch = book_obj.canonical_date(end)
    try:
        if query:
            ok = book_obj.query(start_epoch, end_epoch)
        elif delete:
            ok = book_obj.delete(start_epoch, end_epoch)
        else:
            if slice_name is None:
                raise click.UsageError(
                    "--slice is mandatory unless using -q or -d")
            ok = book_obj.book(slice_name, start_epoch, end_epoch)
    except requests.exceptions.RequestException as exc:
        if hasattr(exc, 'response') and exc.response is not None:
            print(f"HTTP error ({exc.response.status_code}): {exc}")
        else:
            print(f"Could not reach the r2lab API: {exc}")
        ok = False
    sys.exit(0 if ok else 1)


####################
# monitors

from rhubarbe.logger import r2lab_sidecar_logger  # noqa: E402


def set_loggers_level(verbose, debug):
    print("setting logger level on r2lab-sidecar and monitor")
    if debug:
        monitor_logger.setLevel(logging.DEBUG)
        r2lab_sidecar_logger.setLevel('DEBUG')
    elif verbose:
        monitor_logger.setLevel('INFO')
        r2lab_sidecar_logger.setLevel('INFO')
    else:
        monitor_logger.setLevel('WARNING')
        r2lab_sidecar_logger.setLevel('WARNING')


def _monitor_common_options(cycle_key, prometheus_key):
    """Decorator stack shared by monitor commands."""
    def decorator(f):
        f = click.option(
            '--prometheus-port', type=int, show_default=True,
            default=lambda: Config().value('monitor', prometheus_key),
            help="port for Prometheus /metrics endpoint (0 to disable)")(f)
        f = click.option('-d', '--debug', is_flag=True, default=False)(f)
        f = click.option('-v', '--verbose', is_flag=True, default=False)(f)
        f = click.option(
            '-u', '--sidecar-url', show_default=True,
            default=lambda: Config().value('sidecar', 'url'),
            help="url for the sidecar server")(f)
        f = click.option(
            '-c', '--cycle', type=float, show_default=True,
            default=lambda: Config().value('monitor', cycle_key),
            help="Delay to wait between 2 probes")(f)
        return f
    return decorator


@cli.command()
@_monitor_common_options('cycle_nodes', 'prometheus_port_nodes')
@selector_options
def monitornodes(cycle, sidecar_url, verbose, debug, prometheus_port,
                 all_nodes, ranges):
    """Cyclically probe all selected nodes; report status to the sidecar."""
    set_loggers_level(verbose, debug)
    PrometheusExporter().start(prometheus_port)

    selector = build_selector(ranges, all_nodes)
    message_bus = asyncio.Queue()
    display = Display([], message_bus)

    monitor_logger.info({'selected_nodes': selector})
    mon = MonitorNodes(selector.cmc_names(),
                       message_bus=message_bus,
                       cycle=cycle,
                       sidecar_url=sidecar_url,
                       verbose=verbose)

    async def async_main():
        await asyncio.gather(mon.run_forever(), display.run())

    MonitorLoop("monitornodes").run(async_main())


@cli.command()
@_monitor_common_options('cycle_phones', 'prometheus_port_phones')
def monitorphones(cycle, sidecar_url, verbose, debug, prometheus_port):
    """Cyclically probe all known phones; report status to the sidecar."""
    set_loggers_level(verbose, debug)
    PrometheusExporter().start(prometheus_port)

    monitor_logger.info("Using all phones")
    mon = MonitorPhones(cycle=cycle, sidecar_url=sidecar_url, verbose=verbose)
    MonitorLoop("monitorphones").run(mon.run_forever())


@cli.command()
@_monitor_common_options('cycle_pdus', 'prometheus_port_pdus')
@click.argument('names', nargs=-1)
def monitorpdus(cycle, sidecar_url, verbose, debug, prometheus_port, names):
    """Cyclically probe all known PDUs; report status to the sidecar."""
    set_loggers_level(verbose, debug)
    PrometheusExporter().start(prometheus_port)

    monitor_logger.info(f"Monitoring pdus: {list(names) or 'all'}")
    mon = MonitorPdus(cycle=cycle, sidecar_url=sidecar_url,
                      verbose=verbose, names=list(names))
    MonitorLoop("monitorpdus").run(mon.run_forever())


####################


@cli.command()
@click.option('-c', '--cycle', default=None,
              help="Set cycle in seconds; 0 means run only once; "
                   "default from config file.")
@click.option('-d', '--debug', is_flag=True, default=False)
def accountsmanager(cycle, debug):
    """The core of the accounts manager; reserved to root."""
    sys.exit(AccountsManager().main(cycle, debug))


####################


@cli.command()
def inventory():
    """Display inventory."""
    InventoryNodes().display(verbose=True)


####################


@cli.command()
@click.argument('sections', nargs=-1)
def config(sections):
    """Display global configuration."""
    Config().display(list(sections))


####################


@cli.command()
@click.option('-n', '--nodes', 'show_nodes', is_flag=True, default=False,
              help="Show template for /etc/rhubarbe/inventory-nodes.json")
@click.option('-p', '--phones', 'show_phones', is_flag=True, default=False,
              help="Show template for /etc/rhubarbe/inventory-phones.json")
@click.option('-u', '--pdus', 'show_pdus', is_flag=True, default=False,
              help="Show template for /etc/rhubarbe/inventory-pdus.yaml")
def template(show_nodes, show_phones, show_pdus):
    """Show the inventory template files under /etc/rhubarbe."""
    def show(hardware_kind, ext):
        data = (
            resources.files("rhubarbe")
            / f"config/inventory-{hardware_kind}.{ext}.template"
        ).read_bytes()
        print("# =========="
              f" template for /etc/rhubarbe/inventory-{hardware_kind}.json")
        print(data.decode(encoding='utf-8'))

    # default: -n
    if not show_nodes and not show_phones and not show_pdus:
        show_nodes = True

    for kind, ext, selected in (
        ('nodes', 'json', show_nodes),
        ('phones', 'json', show_phones),
        ('pdus', 'yaml', show_pdus),
    ):
        if selected:
            show(kind, ext)


####################
# pdu — click subgroup


@cli.group(cls=AliasedGroup)
@click.option('-v', '--verbose', is_flag=True, default=False)
@click.pass_context
def pdu(ctx, verbose):
    """Manage PDUs (power distribution units).

    \b
    Examples:
      rhubarbe pdu list               # static listing of known PDUs/devices
      rhubarbe pdu list r2lab         # list one pdu_host (static info)
      rhubarbe pdu list jaguar        # works with device names too
      rhubarbe pdu status r2lab       # probe pdu_host live
      rhubarbe pdu status jaguar      # probe device live
      rhubarbe pdu on jaguar
      rhubarbe pdu off panther
      rhubarbe pdu reset n300

    For off/reset, a soft shutdown (`shutdown -h now` over ssh) is
    attempted first when the device is configured for it.
    """
    if verbose:
        import rhubarbe.inventorypdus
        rhubarbe.inventorypdus.VERBOSE = True
    ctx.obj = InventoryPdus.load()


@pdu.command('list')
@click.argument('names', nargs=-1)
@click.pass_obj
def pdu_list(inv, names):
    """List known PDUs and devices (static info, no probe)."""
    inv.display_list(list(names))


@pdu.command('status')
@click.argument('names', nargs=-1)
@click.pass_obj
def pdu_status(inv, names):
    """Probe PDUs and/or devices for their current status."""
    if not names:
        inv.status()
        return
    for name in names:
        try:
            pdu_host = inv.get_pdu_host(name)
            asyncio.run(pdu_host.probe())
            continue
        except ValueError:
            pass
        try:
            device = inv.get_device(name)
            asyncio.run(device.run_action('status'))
        except ValueError:
            print(f"unknown device {name}")


def _pdu_device_action(inv, command, name):
    # like the CMC on/off/reset verbs, this requires a valid lease
    leases = Leases(asyncio.Queue())
    if not check_reservation(leases, verbose=False):
        sys.exit(1)
    try:
        device = inv.get_device(name)
        sys.exit(asyncio.run(device.run_action(command)))
    except ValueError as exc:
        print(exc)
        sys.exit(255)


@pdu.command('on')
@click.argument('name')
@click.pass_obj
def pdu_on(inv, name):
    """Turn on a device."""
    _pdu_device_action(inv, 'on', name)


@pdu.command('off')
@click.argument('name')
@click.pass_obj
def pdu_off(inv, name):
    """Turn off a device (soft shutdown first if configured)."""
    _pdu_device_action(inv, 'off', name)


@pdu.command('reset')
@click.argument('name')
@click.pass_obj
def pdu_reset(inv, name):
    """Reset a device (soft shutdown first if configured)."""
    _pdu_device_action(inv, 'reset', name)


####################
# relay — click subgroup


@cli.group(cls=AliasedGroup,
           aliases={'print-temperatures': 'print',
                    'store-temperatures': 'store'})
def relay():
    """Perform temperature acquisition on the known relays.

    `print-temperatures` and `store-temperatures` are accepted as aliases
    for `print` and `store` respectively.
    """


@relay.command('print')
def relay_print_cmd():
    """Print current temperatures on stdout."""
    InventoryRelays.load().get_temperatures(mode='print')


@relay.command('store')
def relay_store_cmd():
    """Store current temperatures in the database."""
    InventoryRelays.load().get_temperatures(mode='store')


####################


@cli.command()
def relaysapi():
    """Run the relay API service."""
    from rhubarbe.api.relays import run_relays_api_service
    run_relays_api_service()


####################


@cli.command()
def version():
    """Print the rhubarbe version."""
    from rhubarbe.version import __version__
    print(f"rhubarbe version {__version__}")


####################


@cli.command()
@click.argument('subcommand', nargs=-1)
@click.pass_context
def help(ctx, subcommand):  # pylint: disable=redefined-builtin
    """Show help for rhubarbe or a specific subcommand."""
    root = ctx.parent  # context of the `cli` group
    if not subcommand:
        click.echo(cli.get_help(root))
        return
    cmd = cli
    sub_ctx = root
    for name in subcommand:
        if not isinstance(cmd, click.Group):
            raise click.UsageError(f"'{name}' is not a subcommand group")
        next_cmd = cmd.get_command(sub_ctx, name)
        if next_cmd is None:
            raise click.UsageError(f"no such command: {' '.join(subcommand)}")
        sub_ctx = click.Context(next_cmd, info_name=name, parent=sub_ctx)
        cmd = next_cmd
    click.echo(cmd.get_help(sub_ctx))


####################
# entry point


def _translate_dashed_invocation():
    """Translate `rhubarbe-<sub> ...` into `rhubarbe <sub> ...`.

    Looked up against the click registry so it works for both leaf commands
    and subgroups (e.g. `rhubarbe-pdu list jaguar`).
    """
    basename = os.path.basename(sys.argv[0])
    if not basename.startswith('rhubarbe-'):
        return
    sub = basename[len('rhubarbe-'):]
    if sub in cli.commands:
        sys.argv = [sys.argv[0], sub] + sys.argv[1:]


def main():
    """Single entry point for all `rhubarbe*` console scripts."""
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)
    sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', 1)
    _translate_dashed_invocation()
    try:
        cli(prog_name='rhubarbe')
    except MisformedRange as exc:
        print("ERROR:", exc)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        traceback.print_exc()
        print(f"rhubarbe: Something went badly wrong: {exc}")
        sys.exit(1)
