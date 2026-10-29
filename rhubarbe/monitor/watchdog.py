"""
A stall detector for the monitor services

The monitors are known to get stuck every now and then, in a way that is not
well understood yet; the symptom being that nothing gets published anymore.

The watchdog checks periodically 2 things
* that something was successfully sent to the sidecar recently
* that each prober (one per node or phone) completed a probe recently
  - a single stuck prober would not show up in the first check, as the
    other ones keep emitting

if either fails, it logs a snapshot of the internal state
(all asyncio tasks and where they are suspended, connection state, backlog,
open files, probes that did not complete recently) and then exits the
process, so that systemd (Restart=always) brings it back up.
"""

# pylint: disable=logging-fstring-interpolation, missing-function-docstring

import os
import time
import asyncio
import logging

from rhubarbe.logger import monitor_logger as logger


def count_open_files():
    for path in ('/proc/self/fd', '/dev/fd'):
        try:
            return len(os.listdir(path))
        except OSError:
            continue
    return -1


def coroutine_chain(task):
    """
    describe where a task is currently suspended, by following the chain
    of awaited coroutines down to the innermost one
    (Task.get_stack() only returns the outermost frame)
    """
    lines = []
    coro = task.get_coro()
    while coro is not None:
        frame = getattr(coro, 'cr_frame', None) or getattr(coro, 'gi_frame', None)
        if frame is not None:
            code = frame.f_code
            lines.append(f"{code.co_filename}:{frame.f_lineno} in {code.co_name}")
        coro = getattr(coro, 'cr_await', None) or getattr(coro, 'gi_yieldfrom', None)
    return lines


class Watchdog:
    """
    reconnectable: the ReconnectableSidecar whose last_send_ok is monitored
    probers: objects with an 'id' and a 'last_probe_end' attribute,
      used only to enrich the snapshot
    stall_timeout: seconds without a successful send, or without
      a prober completing a probe, before we give up
    """

    def __init__(self, name, reconnectable, probers,
                 stall_timeout=300., check_period=30.):
        self.name = name
        self.reconnectable = reconnectable
        self.probers = probers
        self.stall_timeout = stall_timeout
        self.check_period = check_period

    def snapshot(self):
        now = time.time()
        logger.error(f"watchdog {self.name}: SNAPSHOT {self.reconnectable} "
                     f"open files={count_open_files()}")
        stale = sorted(
            ((now - p.last_probe_end, getattr(p, 'id', repr(p)))
             for p in self.probers),
            reverse=True)
        logger.error(f"watchdog {self.name}: {len(stale)} probers, "
                     f"least recently completed (age in s, id): {stale[:10]}")
        tasks = asyncio.all_tasks()
        logger.error(f"watchdog {self.name}: {len(tasks)} tasks")
        for task in tasks:
            chain = " <- ".join(reversed(coroutine_chain(task)))
            logger.error(f"watchdog {self.name}: task {task.get_name()} "
                         f"suspended at: {chain}")

    def stuck_probers(self):
        """
        the (age, id) of the probers that have not completed a probe
        for more than stall_timeout seconds, oldest first
        """
        now = time.time()
        return sorted(
            ((now - p.last_probe_end, getattr(p, 'id', repr(p)))
             for p in self.probers
             if now - p.last_probe_end >= self.stall_timeout),
            reverse=True)

    async def run(self):
        while True:
            await asyncio.sleep(self.check_period)
            silence = time.time() - self.reconnectable.last_send_ok
            stuck = self.stuck_probers()
            if silence >= self.stall_timeout:
                reason = (f"no successful send for {silence:.0f}s "
                          f"(limit {self.stall_timeout:.0f}s)")
            elif stuck:
                reason = (f"{len(stuck)} prober(s) stuck for more than "
                          f"{self.stall_timeout:.0f}s, "
                          f"(age in s, id): {stuck[:10]}")
            else:
                continue
            logger.error(f"watchdog {self.name}: {reason} - exiting")
            try:
                self.snapshot()
            except Exception:                       # pylint: disable=broad-except
                logger.exception("watchdog: snapshot failed")
            logging.shutdown()
            # not sys.exit(), that would only end this task
            os._exit(3)                             # pylint: disable=protected-access
