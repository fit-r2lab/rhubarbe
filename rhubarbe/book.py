"""
a utility to check if the testbed is free for a given time slot
"""

# could have been inserted into lease.py, but that one is already too big
# and convoluted

import time
from datetime import datetime as DateTime
import argparse

from .config import Config
from .plcapiproxy import PlcApiProxy

class Book:

    def __init__(self, email=None, password=None, verbose=False):
        self.verbose = verbose
        plcapi_url = Config().value('plcapi', 'url')
        self.plcapi_proxy = PlcApiProxy(plcapi_url, email=email, password=password)
        self.leases_hostname = Config().value('plcapi', 'leases_hostname')


    def leases(self, start, end) -> list["PlcLease"]:
        """
        return the leases for the given time slot
        """
        leases = self.plcapi_proxy.GetLeases({'clip': (start, end)})
        # sort by start time
        leases.sort(key=lambda lease: lease['t_from'])
        return leases

    def canonical_date(self, date):
        """
        return a date in the format expected by the plcapi
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
        return epoch + time.timezone

    def date_to_string(self, date):
        """
        return a date in the format expected by the plcapi
        """
        return DateTime.fromtimestamp(date).strftime('%Y-%m-%dT%H:%M')

    def lease_repr(self, lease):
        """
        return a string representation of the lease
        """
        return (
            f"{self.date_to_string(lease['t_from'])} -> "
            f"{self.date_to_string(lease['t_until'])} "
            f"{lease['name']} "
        )

    def query(self, start, end) -> bool:
        leases = (self.leases(start, end))
        if self.verbose:
            print(f"{len(leases)} leases from {self.date_to_string(start)} "
                f"-> {self.date_to_string(end)}")
            for lease in leases:
                print(f"{14*' '}{self.lease_repr(lease)}")
        return not leases

    def book(self, slice, start, end) -> bool:
        if self.verbose:
            print(
                f"booking {slice} from {self.date_to_string(start)} "
                f"until {self.date_to_string(end)}")
        hostname = self.leases_hostname
        try:
            retcod = self.plcapi_proxy.AddLeases(
                    [hostname], slice, start, end
            )
            if 'new_ids' in retcod:
                if self.verbose:
                    print(f"new lease id: {retcod['new_ids'][0]}")
                return True
            else:
                print(f"error: {retcod}")
                return False
        except Exception as exc:
            print(f"exception in AddLeases : {type(exc)}: {exc}")
            return False

    @staticmethod
    def main(*argv) -> bool:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "-q", "--query", action="store_true",
            help="does not book, just checks if the slot is free")
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
        else:
            if args.slice is None:
                parser.error("slice is mandatory unless using -q")
                exit(1)
            return book.book(args.slice, start, end)
