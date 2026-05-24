#!/usr/bin/env python3

"""Entry point for the `rhubarbe*` console scripts.

The real CLI is the click group `rhubarbe.main.cli`; the
`rhubarbe-<sub>` binaries are translated to `rhubarbe <sub> ...`
inside `main.main`.
"""

from rhubarbe.main import main


if __name__ == "__main__":
    main()
