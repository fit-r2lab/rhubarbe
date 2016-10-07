#!/usr/bin/env python3

import sys
import os

# use line buffering on stdout and stderr
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1) # no buffering
sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', 1) # no buffering

import rhubarbe.main
from rhubarbe.selector import MisformedRange

def main():
    supported = rhubarbe.main.supported_subcommands
    command = sys.argv[0]
    args = None
    subcommand = None
    # can we find a supported subcommand in argv[0]
    # i.e. when invoked as rhubarbe-monitor
    for candidate in supported:
        # just checking 'candidate in sys.argv[0]'
        # does not work as it would e.g. find subcommand
        # 'on' when trying to call monitor...
        # instead, we check that the command ends with e.g. -monitor
        if sys.argv[0].endswith("-" + candidate):
            subcommand = candidate
            args = sys.argv[1:]
            break

    # not found that way : subcommand must be argv[1]
    # so we need at least one argv left
    if not subcommand and len(sys.argv) <= 1:
        print("{} needs a subcommand in {{{}}}"
              .format(command, ",".join(supported)))
        exit(1)
        
    if not subcommand:
        subcommand = sys.argv[1]
        args = sys.argv[2:]
        if subcommand not in supported:
            print("Unknown subcommand {} - use one among {{{}}}"
                  .format(subcommand, ",".join(supported)))
            exit(1)
    # do it
    entry_point = getattr(rhubarbe.main, subcommand)
    # remove subcommand from args
    try:
        exit(entry_point(*args))
    except MisformedRange as e:
        print("ERROR: ", e)
        exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("{} {} : Something went badly wrong : {}".format(command, subcommand, e))
        exit(1)

if __name__ == '__main__':
    main()
