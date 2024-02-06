"""
a utility to check if the testbed is free for a given time slot
"""

# could have been inserted into lease.py, but that one is already too big
# and convoluted

import time
from datetime import datetime as DateTime
import argparse

from rhubarbe.config import Config
from rhubarbe.plcapiproxy import PlcApiProxy

class FreeSlot:

    def __init__(self, email=None, password=None):
        plcapi_url = Config().value('plcapi', 'url')
        self.plcapi_proxy = PlcApiProxy(plcapi_url, email=email, password=password)


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
            f"{self.date_to_string(lease['t_from'])} "
            f"{self.date_to_string(lease['t_until'])} "
            f"{lease['name']} "
        )

    @staticmethod
    def main(*argv):
        parser = argparse.ArgumentParser()
        parser.add_argument("-e", "--email", help="email")
        parser.add_argument("-p", "--password", help="password")
        parser.add_argument("-v", "--verbose", help="verbose", action="store_true")
        parser.add_argument(
            "start",
            help="start time format: 2020-01-01T00:00 or 2020-01-01 00:00 or 14:00")
        parser.add_argument("end", help="end time, same format as start time")
        args = parser.parse_args(*argv)
        free_slot = FreeSlot(email=args.email, password=args.password)
        start = free_slot.canonical_date(args.start)
        end = free_slot.canonical_date(args.end)
        leases = (free_slot.leases(start, end))
        if args.verbose:
            print(f"leases from {free_slot.date_to_string(start)} "
                f"until {free_slot.date_to_string(end)}")
            for lease in leases:
                print(free_slot.lease_repr(lease))
        exit(0 if not leases else 1)

if __name__ == "__main__":
    import sys
    FreeSlot.main(sys.argv[1:])
