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

from .logger import logger
from .config import Config
from .plcapiproxy import PlcApiProxy

DEBUG = False
DEBUG = True


class Lease:
    """
    a single lease entry
    """

    wire_timeformat = "%Y-%m-%dT%H:%M:%S%Z"

    def __init__(self, plc_lease):
        try:
            self.leases_hostname = Config().value('plcapi', 'leases_hostname')
            self.owner = plc_lease['name']
            self.lease_id = plc_lease['lease_id']
            self.subjects = [plc_lease['hostname']]
            self.ifrom, self.iuntil = plc_lease['t_from'], plc_lease['t_until']
            self.broken = False
            # this is only to get __repr__ as short as possible

        except Exception as exc:
            self.broken = f"lease broken b/c of exception {exc}"

        if not self.subjects:
            self.broken = "(no component)"

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
        # usual case is that self.subjects == [leases_hostname]
        if (len(self.subjects) == 1
            and self.subjects[0] == self.leases_hostname):
            scope = ""
        else:
            msg = " & ".join(self.subjects)
            scope = f" -> {msg}"
        return (f"{rights_char}{time_char}"
                f" {time_message} {self.owner} {scope}")

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

    def booked_now_by(self, hostname, login):
        """
        tells if this lease is held right now, by that login (if not None)
        or by anyone (if login is None)
        """
        return self.booked_at_by(hostname, time.time(), login)

    def booked_at_by(self, hostname, instant, login):
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
        if hostname not in self.subjects:
            if DEBUG:
                logger.info(f"{hostname} not among subjects {self.subjects}")
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
    # the details of the plcapi_proxy instance where to look for leases

    def __init__(self, message_bus):
        self.message_bus = message_bus
        # don't use os.getlogin() as this gives root if under su
        self.login = pwd.getpwuid(os.getuid())[0]
        # the hostname of the plcapi node that we attach leases to
        self.leases_hostname = Config().value('plcapi', 'leases_hostname')
        plcapi_url = Config().value('plcapi', 'url')
        self.plcapi_proxy = PlcApiProxy(plcapi_url)
        # computed later
        # a list of Lease objects
        self.leases = None
        # the result of GetLeases - essentially as-is
        self.plc_leases = None
        # xxx this is still used by monitornodes
        # should be cleaned up
        self.resources = None

    def __repr__(self):
        if self.leases is None:
            return (f"<Leases from {self.plcapi_proxy} - **(UNFETCHED)**>")
        else:
            return (f"<Leases from {self.plcapi_proxy}"
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
        return any([lease.booked_now_by(self.leases_hostname, login)
                    for lease in self.leases])

    def _booked_now_by_anyone(self):
        # must have run fetch_all() before calling this
        return any([lease.booked_now_by(self.leases_hostname, login=None)
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

    def resource_from_lease(self, plc_lease):
        return {'uuid': plc_lease['lease_id'],
                'slicename': plc_lease['name'],
                'valid_from': self.epoch_to_ui_ts(plc_lease['t_from']),
                'valid_until': self.epoch_to_ui_ts(plc_lease['t_until']),
                'ok': True}

    async def _fetch_leases(self):
        self.leases = None
        try:
            logger.info("Leases are being fetched..")
            self.plc_leases = self.plcapi_proxy.GetLeases(
                {'day': 0}, anonymous=True)
            logger.info(f"{len(self.plc_leases)} leases received")
            # decoded as a list of Lease objects
            self.leases = [Lease(resource) for resource in self.plc_leases]
            self.sort_leases()
            self.resources = [
                self.resource_from_lease(plc_lease)
                for plc_lease in self.plc_leases
            ]

        except Exception as exc:
            if DEBUG:
                print(f"Leases.fetch: exception {exc}")
            traceback.print_exc()
            await self.feedback(
                'leases_error',
                f"cannot get leases from {self} - exception {exc}")

    # this can be used with a fake message queue, it's synchroneous
    def print(self):
        print(5 * '-', self,
              "with special privileges"
              if self.has_special_privileges() else "")
        if self.leases is not None:
            def rights_char(lease):
                # B like broken
                if lease.broken:
                    return 'B'
                # S like super user
                if self.has_special_privileges():
                    return 'S'
                # * for yes you can use it
                if lease.booked_now_by(self.leases_hostname, self.login):
                    return '*'
                return ' '
            for i, lease in enumerate(self.leases):
                msg = lease.__repr__(rights_char=rights_char(lease))
                print(f"{i+1:3d} {msg}")

    # material to create and modify leases
    @staticmethod
    def to_epoch(incoming):
        if not incoming:
            return time.time()
        if isinstance(incoming, (int, float)):
            return incoming
        patterns = [
            # fill in year
            "%Y-{}:00%Z",  # "%Y-{}:00",
            "%Y-%m-{}:00%Z",  # "%Y-%m-{}:00",
            "%Y-%m-%dT{}:00%Z",  # "%Y-%m-%dT{}:00",
            "%Y-%m-%dT{}:00:00%Z",  # "%Y-%m-%dT{}:%M:00",
        ]

        for pattern in patterns:
            fill = time.strftime(pattern).format(incoming)
            try:
                struct_time = time.strptime(fill, Lease.wire_timeformat)
                return int(time.mktime(struct_time))
            except Exception:
                pass

    @staticmethod
    def check_user(user):
        return os.path.exists(f"/home/{user}")

    async def _add_lease(self, owner, input_from, input_until):
        if owner != 'root':
            if not self.check_user(owner):
                print(f"user {owner} not found under /home - giving up")
                logger.error(f"Unknown user {owner}")
                return
        t_from = Leases.to_epoch(input_from)
        if not t_from:
            print(f"invalid time from: {input_from}")
            return
        t_until = Leases.to_epoch(input_until)
        if not t_until:
            print(f"invalid time until: {input_until}")
            return
        # just making sure
        try:
            hostname = self.leases_hostname
            retcod = self.plcapi_proxy.AddLeases(
                [hostname], owner, t_from, t_until)
            if 'new_ids' in retcod:
                # do we want to automatically
                # recompute the index ?
                print("OK")
                # force next reload
                self.leases = None
            elif 'errors' in retcod and retcod['errors']:
                for error in retcod['errors']:
                    print(f"error: {error}")
        except Exception as exc:
            print('Error', f"Cannot add lease {type(exc)}: {exc}")
            traceback.print_exc()

    def get_lease_by_rank(self, lease_rank):
        try:
            irank = int(lease_rank)
            return self.leases[irank - 1]
        except Exception:
            pass

    async def _update_lease(self, lease_rank, input_from, input_until):
        update_fields = {}
        if not input_from and not input_until:
            logger.info("update_lease : nothing to do")
            return
        if input_from:
            t_from = Leases.to_epoch(input_from)
            if not t_from:
                print(f"invalid time from: {input_from}")
                return
            update_fields['t_from'] = t_from
        if input_until:
            t_until = Leases.to_epoch(input_until)
            if not t_until:
                print(f"invalid time until: {input_until}")
                return
            update_fields['t_until'] = t_until
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if not the_lease:
            print(f"Cannot find lease with rank {lease_rank}")
            return
        lease_ids = [the_lease.lease_id]
        retcod = self.plcapi_proxy.UpdateLeases(lease_ids, update_fields)
        if 'errors' in retcod and retcod['errors']:
            for error in retcod['errors']:
                print(f"error: {error}")
        else:
            print("OK")
            # force next reload
            self.leases = None

    async def _delete_lease(self, lease_rank):
        # lease_rank could be a rank as displayed by self.print()
        the_lease = self.get_lease_by_rank(lease_rank)
        if not the_lease:
            print(f"Cannot find lease with rank {lease_rank}")
            return
        lease_ids = [the_lease.lease_id]
        retcod = self.plcapi_proxy.DeleteLeases(lease_ids)
        if retcod == 1:
            print("OK")
            # force next reload
            self.leases = None
        else:
            print("not deleted")

    async def main(self, interactive):
        await self.fetch_all()
        self.print()
        if not interactive:
            return 0
        try:
            result = await self.interactive()
            return result
        except (KeyboardInterrupt, EOFError):
            print("Bye")
            return 1

    async def interactive(self):
        help_message = """
Enter one of the letters inside [], and answer the questions

A lease index is a number as shown on the left in the leases list

Times can be entered simply as
* just 14, or 14:00, for today at 2p.m.
* 14:30 for today at 2:30 p.m., or
* 27T10:30 for the 27th this month at 10:30 a.m., or
* 12-10T01:00 for the 12th december this year at 1 a.m., or
* 2016-01-02T08:00 for January 1st, 2016, at 8:00

In all the above cases, times will be understood as local time (France).

Leaving a time empty means either 'now', or 'do not change',
depending on the context
"""
        # interactive mode
        while True:
            current_time = time.strftime("%H:%M")
            answer = input(f"{current_time} - Enter command "
                           f"([l]ist, [a]dd, [u]pdate, "
                           f"[d]elete, [r]efresh, [q]uit : ")
            char = answer[0].lower() if answer else 'l'
            if char == 'l':
                self.print()
            elif char == 'a':
                if self.has_special_privileges():
                    owner = input("For slice name : ")
                else:
                    owner = self.login
                time_from = input("From : ")
                time_until = input("Until : ")
                print(f"owner={owner}")
                await self._add_lease(owner, time_from, time_until)
                await self.fetch_all()
            elif char == 'u':
                rank = input("Enter lease index : ")
                time_from = input("From : ")
                time_until = input("Until : ")
                await self._update_lease(rank, time_from, time_until)
                await self.fetch_all()
            elif char == 'd':
                rank = input("Enter lease index : ")
                await self._delete_lease(rank)
                await self.fetch_all()
            elif char == 'r':
                await self.refresh()
                self.print()
            elif char == 'q':
                print('bye')
                break
            else:
                print(help_message)
        return 0
