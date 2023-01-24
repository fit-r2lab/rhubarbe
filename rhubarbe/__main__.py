#!/usr/bin/env python3

#pylint: disable=missing-module-docstring, missing-class-docstring, missing-function-docstring
import sys
import os
import traceback

import rhubarbe.main
from .selector import MisformedRange


class Rhubarbe:
    def main(self):
        """
        the entry point for all rhubarbe* commands
        """

        # use line buffering on stdout and stderr
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1) # no buffering
        sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', 1) # no buffering

        #
        supported = rhubarbe.main.supported_subcommands
        command = sys.argv[0]
        args = None
        subcommand = None
        # can we find a supported subcommand in argv[0]
        # i.e. when invoked as rhubarbe-monitornodes
        for candidate in supported:
            # just checking 'candidate in sys.argv[0]'
            # does not work as it would e.g. find subcommand
            # 'on' when trying to call monitornodes...
            # instead, we check that the command ends with e.g. -monitornodes
            if sys.argv[0].endswith("-" + candidate):
                subcommand = candidate
                args = sys.argv[1:]
                break

        # not found that way : subcommand must be argv[1]
        # so we need at least one argv left
        if not subcommand and len(sys.argv) <= 1:
            print(f"{command} needs a subcommand in {{{','.join(supported)}}}")
            exit(1)

        if not subcommand:
            subcommand = sys.argv[1]
            args = sys.argv[2:]
            if subcommand not in supported:
                print(f"Unknown subcommand {subcommand} "
                    f"- use one among {{{','.join(supported)}}}")
                exit(1)
        # do it
        entry_point = getattr(rhubarbe.main, subcommand)
        # remove subcommand from args
        try:
            exit(entry_point(*args))
        except MisformedRange as exc:
            print("ERROR: ", exc)
            exit(1)
        except Exception as exc:                        # pylint: disable=broad-except
            traceback.print_exc()
            print(f"{command} {subcommand} : Something went badly wrong : {exc}")
            exit(1)

def main():
    Rhubarbe().main()
