"""
Leases management :
download from r2labapi service,
creation update deletions,
and minimal verifications
"""

# c0111 no docstrings yet
# w1202 logger & format
# w0703 catch Exception
# r1705 else after return
# pylint: disable=c0111, w1202, r1705, w0703

import os
import pwd
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests

from .logger import logger
from .config import Config
from .r2labapiproxy import R2labApiProxy, iso_to_epoch, epoch_to_iso

DEBUG = False
DEBUG = True


class Lease:
    """
    a single lease entry
    """

    wire_timeformat = "%Y-%m-%dT%H:%M:%S%Z"

    def __init__(self, api_lease):
        try:
            self.owner = api_lease.get('slice_name') or '(unknown)'
            self.lease_id = api_lease['id']
            self.ifrom = iso_to_epoch(api_lease['t_from'])
            self.iuntil = iso_to_epoch(api_lease['t_until'])
            self.broken = False

        except Exception as exc:
            self.broken = f"lease broken b/c of exception {exc}"

    # show a 2-chars prefix
    # one for rights (computed by caller - not shown if not provided)
    # one for situation wrt now: < for past, = for current, > for future
    def __repr__(self, rights_char=None):
        rights_char = ('B' if self.broken
                       else rights_char if rights_char
                       else '?')
        now = time.time()
        if self.iuntil < now:
            time_char = '<'
        elif self.ifrom < now:
            time_char = '='
        else:
            time_char = '>'
        # show second day only when they are different
        second_day = not self.same_date(self.ifrom, self.iuntil)
        time_message = (f"from {self.human(self.ifrom, show_timezone=False)}"
                        f" until {self.human(self.iuntil, show_date=second_day)}")
        return (f"{rights_char}{time_char}"
                f" {time_message} {self.owner}")

    def sort_key(self):
        """
        Sort on start time
        """
        return self.ifrom

    @staticmethod
    def human(epoch, show_date=True, show_timezone=True):
        """
        human-readable format
        """
        timeformat = ""
        timeformat += "%H:%M"
        if show_date:
            timeformat += " [on %m-%d]"
        if show_timezone:
            timeformat += " %Z"
        return time.strftime(timeformat, time.localtime(epoch))

    @staticmethod
    def same_date(epoch1, epoch2):
        """
        Returns True if both instants belong in the same day
        for local timezone
        """
        return (
            time.strftime("%y-%m-%d", time.localtime(epoch1))
            ==
            time.strftime("%y-%m-%d", time.localtime(epoch2))
        )

    def booked_now_by(self, login):
        """
        tells if this lease is held right now, by that login (if not None)
        or by anyone (if login is None)
        """
        return self.booked_at_by(time.time(), login)

    def booked_at_by(self, instant, login):
        """
        tells if this lease is held at that time, by that login (if not None)
        or by anyone (if login is None)
        """
        if self.broken:
            if DEBUG:
                logger.info(f"ignoring broken lease {self}")
            return False
        if not self.ifrom <= instant <= self.iuntil:
            if DEBUG:
                logger.info(f"{self} : wrong timerange")
            return False
        if login is not None and not self.owner == login:
            if DEBUG:
                logger.info(
                    f"login {login} is not owner - actual owner is {self.owner}")
            return False
        return self


class Leases:                                           # pylint: disable=r0902
    """
    A list of leases as downloaded from the API
    """

    ADMIN_PREFIX = 'r2lab-'

    @staticmethod
    def resolve_time_keyword(value):
        """
        Resolve a time keyword or ISO timestamp to an epoch.
        Supported keywords: 'now', 'today', 'tomorrow'
        """
        if value == 'now':
            return time.time()
        if value == 'today':
            today = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0)
            return today.timestamp()
        if value == 'tomorrow':
            tomorrow = (datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1))
            return tomorrow.timestamp()
        # try ISO format
        return iso_to_epoch(value)

    def __init__(self, message_bus):
        self.message_bus = message_bus
        # don't use os.getlogin() as this gives root if under su
        self.login = pwd.getpwuid(os.getuid())[0]
        the_config = Config()
        api_url = the_config.value('r2labapi', 'url')
        self.resource_name = the_config.value('r2labapi', 'resource_name')
        # no token: reads are public, writes will prompt for credentials
        self.proxy = R2labApiProxy(api_url)
        # display filters (epochs); set before calling main()
        self.after_filter = None
        self.before_filter = None
        self.no_admin = False
        # computed later
        # a list of Lease objects
        self.leases = None
        # the raw API response
        self.api_leases = None
        # xxx this is still used by monitornodes
        # should be cleaned up
        self.resources = None

    def __repr__(self):
        if self.leases is None:
            return (f"<Leases from {self.proxy} - **(UNFETCHED)**>")
        else:
            return (f"<Leases from {self.proxy}"
                    f" - {len(self.leases)} lease(s)>")

    async def feedback(self, field, msg):
        """
        send feedback, for displaying or monitoring
        """
        await self.message_bus.put({field: msg})

    def has_special_privileges(self):
        """
        check for being run as root
        """
        privileged = Config().value('accounts', 'privileged')
        names = privileged.split(',')
        return self.login in names

    async def booked_now_by_me(self, *, root_allowed=True):
        """
        fetch leases and return a bool that says if current login has a lease
        if root_allowed is True, then this function will return True
          for privileged users, no matter what else
        if root_allowed is false, privileged users go through the usual
          process, which most of the time has this function answering False
        """
        return await self.booked_now_by(login=self.login,
                                        root_allowed=root_allowed)

    async def booked_now_by(self, login, *, root_allowed=True):
        if root_allowed and self.has_special_privileges():
            return True
        try:
            await self.fetch_all()
            return self._booked_now_by_login(login)
        except Exception as exc:
            await self.feedback('info',
                                f"Could not fetch leases : {exc}")
            return False

    async def booked_now_by_anyone(self):
        """
        fetch leases and return a bool that says if a lease is currently valid
        """
        try:
            await self.fetch_all()
            return self._booked_now_by_anyone()
        except Exception as exc:
            await self.feedback('info', f"Could not fetch leases : {exc}")
            return False

    # the following 2 methods assume the leases have been fetched
    def _booked_now_by_login(self, login):
        # must have run fetch_all() before calling this
        return any([lease.booked_now_by(login)
                    for lease in self.leases])

    def _booked_now_by_anyone(self):
        # must have run fetch_all() before calling this
        return any([lease.booked_now_by(login=None)
                    for lease in self.leases])

    async def fetch_all(self):
        """
        makes sure all required data is available
        (turns out only the leases are required for now)
        """
        await self.fetch_leases()

    async def refresh(self):
        self.leases = None
        await self.fetch_all()

    def sort_leases(self):
        self.leases.sort(key=Lease.sort_key)

    async def fetch_leases(self):
        if self.leases is not None:
            return self.leases
        await self._fetch_leases()
        return self.leases

    # xxx stolen from r2lab.inria.fr : because we still use
    # a data model inspired from when we had OMF
    @staticmethod
    def epoch_to_ui_ts(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    def resource_from_lease(self, api_lease):
        return {'uuid': api_lease['id'],
                'slicename': api_lease.get('slice_name', ''),
                'valid_from': self.epoch_to_ui_ts(
                    iso_to_epoch(api_lease['t_from'])),
                'valid_until': self.epoch_to_ui_ts(
                    iso_to_epoch(api_lease['t_until'])),
                'ok': True}

    async def _fetch_leases(self):
        self.leases = None
        try:
            logger.info("Leases are being fetched..")
            params = {}
            if self.after_filter is not None:
                params['after'] = epoch_to_iso(self.after_filter)
            else:
                # default for non-CLI callers: from today onwards
                today = datetime.now(tz=timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0)
                params['after'] = today.isoformat()
            if self.before_filter is not None:
                params['before'] = epoch_to_iso(self.before_filter)
            self.api_leases = self.proxy.get_leases(**params)
            logger.info(f"{len(self.api_leases)} leases received")
            # decoded as a list of Lease objects
            self.leases = [Lease(entry) for entry in self.api_leases]
            self.sort_leases()
            self.resources = [
                self.resource_from_lease(api_lease)
                for api_lease in self.api_leases
            ]

        except requests.exceptions.RequestException as exc:
            if hasattr(exc, 'response') and exc.response is not None:
                message = (f"HTTP error ({exc.response.status_code})"
                           f" from {self.proxy}")
            else:
                message = f"cannot reach r2lab API at {self.proxy}: {exc}"
            logger.error(message)
            print(message)
            await self.feedback('leases_error', message)
        except Exception as exc:
            if DEBUG:
                print(f"Leases.fetch: exception {exc}")
            traceback.print_exc()
            await self.feedback(
                'leases_error',
                f"cannot get leases from {self} - exception {exc}")

    def filtered_leases(self):
        """
        Apply display filters (after, before, no_admin)
        on the fetched leases.
        """
        if self.leases is None:
            return []
        result = self.leases
        if self.after_filter is not None:
            # keep leases that haven't ended before the filter
            result = [l for l in result if l.iuntil > self.after_filter]
        if self.before_filter is not None:
            # keep leases that start before the filter
            result = [l for l in result if l.ifrom < self.before_filter]
        if self.no_admin:
            result = [l for l in result
                      if not l.owner.startswith(self.ADMIN_PREFIX)]
        return result

    # this can be used with a fake message queue, it's synchroneous
    def print(self):
        if self.leases is None:
            print("Could not fetch leases")
            return
        displayed = self.filtered_leases()
        # line 1: time window
        time_parts = []
        if self.after_filter is not None:
            time_parts.append(f"after {Lease.human(self.after_filter)}")
        if self.before_filter is not None:
            time_parts.append(f"before {Lease.human(self.before_filter)}")
        print(f"----- From {', '.join(time_parts)}" if time_parts
              else "No time filter")
        # line 2: count + extras
        extras = []
        if self.no_admin:
            extras.append(f"hiding {self.ADMIN_PREFIX}*")
        if self.has_special_privileges():
            extras.append("with special privileges")
        suffix = f" ({', '.join(extras)})" if extras else ""
        print(f"--- {len(displayed)} lease(s){suffix}")
        def rights_char(lease):
            # B like broken
            if lease.broken:
                return 'B'
            # S like super user
            if self.has_special_privileges():
                return 'S'
            # * for yes you can use it
            if lease.booked_now_by(self.login):
                return '*'
            return ' '
        for i, lease in enumerate(displayed):
            msg = lease.__repr__(rights_char=rights_char(lease))
            print(f"{i+1:3d} {msg}")

    async def main(self):
        await self.fetch_all()
        self.print()
        return 0
