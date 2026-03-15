"""
a utility to check if the testbed is free for a given time slot
"""

# could have been inserted into lease.py, but that one is already too big
# and convoluted

import time
from datetime import datetime as DateTime
import argparse

import requests

from .config import Config
from .r2labapiproxy import R2labApiProxy, iso_to_epoch, epoch_to_iso

class Book:

    def __init__(self, email=None, password=None, verbose=False):
        self._verbose = verbose
        the_config = Config()
        api_url = the_config.value('r2labapi', 'url')
        self.resource_name = the_config.value('r2labapi', 'resource_name')
        self.proxy = R2labApiProxy(api_url)
        if email and password:
            self.proxy.login(email, password)

    def verbose(self, *args, **kwargs):
        if self._verbose:
            print(*args, **kwargs)


    def leases(self, start, end) -> list:
        """
        return the leases for the given time slot
        """
        # after = t_until > start, before = t_from < end
        # i.e. leases that overlap with [start, end]
        api_leases = self.proxy.get_leases(
            after=epoch_to_iso(start),
            before=epoch_to_iso(end),
        )
        # sort by start time; ISO strings sort lexicographically
        api_leases.sort(key=lambda lease: lease['t_from'])
        return api_leases

    def canonical_date(self, date):
        """
        return a date as a Unix epoch
        tries to be flexible and to understand various formats
        """
        # if the date is given as just hh:mm, assume it is today
        if len(date) == 5:
            date = f"{DateTime.now().strftime('%Y-%m-%d')}T{date}"
        formats = [
            '%Y-%m-%dT%H:%M',
            '%Y-%m-%d %H:%M',
        ]
        local = None
        for format in formats:
            try:
                local = DateTime.strptime(date, format)
                break
            except ValueError:
                pass
        if local is None:
            raise ValueError(f"cannot parse date {date}")
        epoch = int((local - DateTime(1970, 1, 1)).total_seconds())

        # translate to UTC
        is_dst = time.daylight and time.localtime().tm_isdst > 0
        utc_offset = (time.altzone if is_dst else time.timezone)
        return epoch + utc_offset

    def date_to_string(self, date):
        """
        return a human-readable date string from an epoch
        """
        return DateTime.fromtimestamp(date).strftime('%Y-%m-%dT%H:%M')

    def lease_repr(self, lease):
        """
        return a string representation of the lease
        """
        t_from = iso_to_epoch(lease['t_from'])
        t_until = iso_to_epoch(lease['t_until'])
        return (
            f"{self.date_to_string(t_from)} -> "
            f"{self.date_to_string(t_until)} "
            f"{lease.get('slice_name', '')} "
        )

    def query(self, start, end) -> bool:
        """
        return True if the time slot is free
        """
        leases = self.leases(start, end)
        self.verbose(
            f"{len(leases)} leases from {self.date_to_string(start)} "
            f"-> {self.date_to_string(end)}")
        for lease in leases:
            self.verbose(f"{14*' '}{self.lease_repr(lease)}")
        return not leases

    def book(self, slice_name, start, end) -> bool:
        """
        book the time slot - return True if successful
        """
        self.verbose(
            f"booking {slice_name} from {self.date_to_string(start)} "
            f"until {self.date_to_string(end)}")
        try:
            lease = self.proxy.create_lease({
                'resource_name': self.resource_name,
                'slice_name': slice_name,
                't_from': epoch_to_iso(start),
                't_until': epoch_to_iso(end),
            })
            self.verbose(f"new lease id: {lease['id']}")
            return True
        except requests.exceptions.HTTPError as exc:
            if exc.response.status_code == 409:
                print(f"conflict: the slot is not free")
            else:
                print(f"HTTP error creating lease: {exc}")
            return False
        except Exception as exc:
            print(f"exception creating lease: {type(exc)}: {exc}")
            return False

    def delete(self, start, end) -> bool:
        """
        spots the - expected single - lease during the time slot
           and deletes it
        implements the same logic as the website, i.e.:
        - a lease in the past is not deleted
        - a currently running lease is updated/shortened so as to release
          the testbed but still keep track of that lease
        - a future lease - or a shortly currently lease - is deleted

        return True if everything went well
        """

        # find the lease
        leases = self.leases(start, end)
        if len(leases) != 1:
            print(f"error: from {self.date_to_string(start)} "
                f"until {self.date_to_string(end)}")
            print(f"expected a single lease, found {len(leases)} leases")
            return False

        lease = leases[0]
        lease_id = lease['id']
        t_from = iso_to_epoch(lease['t_from'])
        t_until = iso_to_epoch(lease['t_until'])
        now = time.time()

        # if the lease is in the past, do nothing
        if t_until < now:
            self.verbose(f"won't delete lease in the past")
            return False

        # how long has it been running ?
        resource = self.proxy.get_resource_by_name(self.resource_name)
        granularity = resource['granularity']
        started = now - t_from
        # it's been running for more than one granularity, update it
        if started > granularity:
            # we keep that many grains
            nb_grains = int(started // granularity)
            new_end = t_from + nb_grains * granularity
            self.verbose(
                f"updating lease {lease_id} until {self.date_to_string(new_end)}")
            try:
                self.proxy.update_lease(
                    lease_id, {'t_until': epoch_to_iso(new_end)})
                return True
            except Exception as exc:
                print(f"exception in update_lease: {type(exc)}: {exc}")
                return False

        # still here ? the lease has not yet started,
        # or it has been running for less than one granularity
        # delete it
        self.verbose(f"deleting future lease {lease_id}")
        try:
            self.proxy.delete_lease(lease_id)
            self.verbose(f"successful")
            return True
        except Exception as exc:
            print(f"exception in delete_lease: {type(exc)}: {exc}")
            return False

    @staticmethod
    def main(*argv) -> bool:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "-q", "--query", action="store_true",
            help="does not book, just checks if the slot is free")
        parser.add_argument(
            "-d", "--delete", action="store_true",
            help="delete the lease for the given time slot")
        parser.add_argument(
            "-s", "--slice",
            help="slice name - mandatory unless using -q")
        parser.add_argument(
            "-e", "--email", help="email")
        parser.add_argument(
            "-p", "--password", help="password")
        parser.add_argument(
            "-v", "--verbose", action="store_true",
            help="be verbose")
        parser.add_argument(
            "start",
            help="start time format: 2020-01-01T00:00 or 2020-01-01 00:00 or 14:00")
        parser.add_argument(
            "end",
            help="end time, same format as start time")
        args = parser.parse_args(*argv)
        book = Book(
            email=args.email, password=args.password, verbose=args.verbose)
        start = book.canonical_date(args.start)
        end = book.canonical_date(args.end)
        if args.query:
            return book.query(start, end)
        elif args.delete:
            return book.delete(start, end)
        else:
            if args.slice is None:
                parser.error("slice is mandatory unless using -q")
                exit(1)
            return book.book(args.slice, start, end)
