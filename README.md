# Summary

`rhubarbe` is an `asyncio`/`python` package that manages the [R2Lab testbed](https://r2lab.inria.fr) at Inria Sophia Antipolis: it controls the nodes through their CMC cards, loads and saves OS images on them with `frisbee`, runs user containers on them, and feeds the monitoring services. It can be installed from `pypi` (see also `INSTALLING.md` for more details):

    pip3 install rhubarbe

It requires python 3.12 or later.

Most of the functions are rather intrusive, and for this reason they are connected to an authorization system: they require the user to have obtained a lease (reservation), applicable to the current time and day, before the tool can be used. Note that the `root` user is unconditionally granted permission.

# Purpose

This is the software that runs the R2lab testbed. It covers the following features:

* `on`, `off`, `reset`, `status`, and the like: managing nodes status through their CMC cards: status of the motherboard and of the USRP extension (`usrpon`, `usrpoff`, `usrpstatus`);
* `load`, `save`, `images`, `resolve`, `share`: loading and saving images on nodes, and managing the repository of images; `rescue` leaves nodes on the frisbee image for investigation;
* `docker`: running user containers on nodes, see below and `DOCKER.md`;
* `wait`: waiting for nodes to be reachable through ssh;
* `leases` and `book`: displaying and creating reservations on the testbed;
* `pdu` and `relay`: managing power-distribution units and temperature relays;
* `monitornodes`, `monitorphones`, `monitorpdus`: services that feed the sidecar server with live info about the testbed status;
* `accountsmanager`: propagates the changes in the leases API into actual Unix accounts and related authorized keys.

With these additional benefits:

* single configuration file in `/etc/rhubarbe/rhubarbe.conf`, individual settings can be
  overridden at either

  * system-level in `/etc/rhubarbe/rhubarbe.conf.local`,
  * user-level in `~/.rhubarbe.conf`, or
  * directory-level in `./rhubarbe.conf`;
* all commands accept a timeout
* all commands return a reliable code. When everything goes fine for all subject nodes they return 0, and 1 otherwise
* `load` and `wait` come with a `curses` mode that lets you visualize every node individually; very helpful when something goes wrong with a large number of nodes, so you can pinpoint which node is not behaving as expected.


# Primer

## Image loading
To load your `fedora-44` image on 18 nodes simultaneously:

    rhubarbe load -i fedora-44 1-18 &

You can safely load another batch of nodes at the same time, maybe with a different bandwidth

    rhubarbe load -i ubuntu-26 -b 200 19-36 &

To see what images are available, `rhubarbe images`; to see which file a name resolves to, `rhubarbe resolve ubuntu-26`.

## Controlling nodes
To turn nodes on, or off, or to reset (send `Ctrl-Alt-Del`) a set of nodes, use the following commands.

    # turn on nodes 1 to 5
    rhubarbe on 1-5
    # turn off node 6
    rhubarbe off 6
    # reset node 7 (needs to be already on)
    rhubarbe reset 7
    # see if node 8 is on or off
    rhubarbe status 8

## Controlling USRP extensions
Nodes that have a USRP hardware can have the USRP extension handled in a similar way

    # turn on the USRP extension on node 11
    rhubarbe usrpon 11
    # turn off the USRP extension on node 12
    rhubarbe usrpoff 12
    # get status of the USRP extension on node 13
    rhubarbe usrpstatus 13

## Waiting for a node

To wait for nodes 1, 3, 5, 7 and 9 to be reachable through ssh :

    rhubarbe wait 1-9-2

## Image saving
To save the image of node 10, just do this

    rhubarbe save 10 -o image-name

 or rather, if you'd like to specify a comment for bookkeeping :

     rhubarbe save 10 -o image-name -c 'this will end up in /etc/rhubarbe-image right in the image'

## Running a container on nodes

Instead of building a node image, one can run any docker image on top of a node that runs a docker-enabled host image:

    # burn the docker host image, then pull and run a container on node 11
    rhubarbe docker load -i ubuntu:latest 11
    # the node already runs the host image: just replace the container
    rhubarbe docker load -k -i fedora:latest 11
    # save the container as an image in the registry
    rhubarbe docker save 11 -o my-experiment

`ssh fit11` then lands inside the container. What this requires from the host images, the registries and the container images is described in `DOCKER.md`; the image side is documented in the `r2lab-imaging` repository.


# How to use

## List of subcommands

The python entry point is named `rhubarbe` but it should be called with an additional subcommand. Each subcommand is also available under a dashed name, e.g. `rhubarbe-load` is the same as `rhubarbe load`.

    $ rhubarbe --help           # all the subcommands
    $ rhubarbe load --help      # details on one of them
    $ rhubarbe help docker load # same, for nested subcommands

Subcommands can be abbreviated as long as that is unambiguous.

## Invoking : node scope

Most commands expect a list of nodes as its arguments

    $ rhubarbe load [-i filename] 1 4 5

The arguments, known as a *node_spec* can be individual nodes, ranges, or even steps, like e.g.

* individual nodes
  * `$ rhubarbe load 1 3 fit8 reboot12` for working on nodes, well
  * 1, 3, 8 12
* ranges
  * `$ rhubarbe load 1-3 8-10` on nodes
  *  1, 2, 3, 8, 9, 10
* steps : in a python-like manner, from-to-step:
  * `$ rhubarbe load 1-10-2` on nodes
  *  1 3 5 7 9
* all nodes
  * `$ rhubarbe load -a` on nodes
  * 1 through 37 on `faraday.inria.fr` (exact scope being defined in the configuration)
* negation
  * `$ rhubarbe load -a ~10-20` on nodes
  * 1 to 9, and 21 to 37

* if no node argument is provided on the command line, the value of the `NODES` environment variable is used. So you can select your set of nodes once, and just use the commands without arguments
  * `$ export NODES="12-15 18-24"`
  * `$ rhubarbe load`
  * `$ rhubarbe wait`

## Leases

* `rhubarbe leases` is offered to all users to inspect the current leases in the system; by default it shows the leases from now until midnight. `-u` shows all upcoming leases, `-c` checks that you currently have one
* `rhubarbe book START END -s SLICE` books a lease (or with `-q` just checks that the slot is free, or with `-d` deletes it)

## Logging

* all logging goes into a file named `rhubarbe.log` in the home directory of the user running the command,
* except for the monitoring services (`monitornodes`, `monitorphones`, `monitorpdus`) that log on stdout, which is meant to be collected by `systemd`/`journald`.

# Installation

## Core

You need `python-3.12` or higher, and installation can be achieved simply with

    pip3 install rhubarbe

## Update

    pip3 install --upgrade rhubarbe

## Development and deployment

For development, `pip install -e .`. The testbed hosts run it from a virtualenv created with `uv`, from the versions pinned in `uv.lock` (after changing dependencies, run `uv lock` and commit `uv.lock`); `make preplab` and `make r2lab` push the working tree to the preplab and to production respectively, see the `Makefile` and `CLAUDE.md`.

## Other libraries

All the dependencies are listed in `pyproject.toml`, and will be installed automatically. The main ones:

* `click` for the command line
* `telnetlib3` for invoking `frisbee` on the nodes
* `aiohttp` for talking to the CMC cards
* `asyncssh` for talking to ssh (`rhubarbe wait`, `rhubarbe docker`, and the monitors);
* `asynciojobs` to orchestrate the asynchronous jobs
* `progressbar33` is used in rendering progress bar in the regular display (i.e. without the `-c` option)
* `fastapi`, `websockets`, `prometheus_client`, `dataclass_wizard` and `pandas` for the services and the PDUs and relays.

# Setting up

## Inventory

The tool needs a mapping between hostnames and MAC addresses - essentially for messing with pxelinux *nextboot* symlinks. It reads inventories in YAML files, whose locations are set in the configuration (`inventory_*_path`):

* `/etc/rhubarbe/inventory-nodes.yaml`: the nodes, top-level `nodes:` list
* `/etc/rhubarbe/inventory-phones.yaml`: the phones
* `/etc/rhubarbe/inventory-pdus.yaml`: the power-distribution units
* `/etc/rhubarbe/inventory-relays.yaml`: the temperature relays

Templates for all of them are shipped in the package, in `rhubarbe/config/`. For nodes, it looks like this (only `cmc` and `control` are needed):

    # less /etc/rhubarbe/inventory-nodes.yaml
    nodes:
      - cmc:
          hostname: reboot01
          ip: 192.168.1.1
          mac: "02:00:00:00:00:01"
        control:
          hostname: fit01
          ip: 192.168.3.1
          mac: "00:03:1d:0e:03:19"
      # ... etc

`rhubarbe inventory` displays what was found.

## Configuration

In short: see `rhubarbe config`, and the file `/etc/rhubarbe/rhubarbe.conf`

Configuration is done through a collection of files, which are loaded in this order if they exist:

 * `/etc/rhubarbe/rhubarbe.conf` (or rather the copy of it that ships with the package)
 * `/etc/rhubarbe/rhubarbe.conf.local`
 * `~/.rhubarbe.conf`
 * `./rhubarbe.conf`
 * `./rhubarbe.conf.local`

So in essence, there is

 * a (mandatory) system-wide config, that ships with the package and that contains all variable definitions;
 * given that it is likely to be overwritten at anytime by an upgrade, you can store your own system-wide changes in the (optional) file `/etc/rhubarbe/rhubarbe.conf.local` - this is also the place for secrets like tokens;
 * then each user may override any value she likes,
 * and finally one can be even more specific and configure things at a directory level.

 The first file will come with all the settings defined, but any of the other ones does not need to be complete and can just redefine one variable if needed.

Any variable can also be given a value specific to a host, e.g. `bandwidth.faraday = 500` overrides `bandwidth` only on the host named `faraday`.

Format is known as a `.ini` file, should be straightforward. Just beware to **not mention quotes** around strings, as such quotes end up in the python string verbatim.

## Authorization system

Among things to be configured are, in the `r2labapi` section:

* `url`: the URL of the R2Lab REST API, which keeps track of the leases,
* `resource_name`: the name of the resource under which leases are recorded on the testbed, and
* `admin_token`: the token used by the services that need privileged access; it belongs in `rhubarbe.conf.local`.

This is an admittedly specific policy for R2Lab, since we want the reservations to be made on the testbed as a whole (to ensure reproducibility).

## Containers

The `[docker]` section configures `rhubarbe docker`: the default host image, the registry that `docker save` pushes to, an optional pull-through mirror, the host's ssh port and the static sshd injected in containers. See `DOCKER.md`.

# Testing

There are no automated tests. Features are checked by hand on the testbed, with the exception of `tests/docker_smoke.py`, a script that exercises `rhubarbe docker load` on real nodes.

# TODO

* in terms of the cleanup regarding display.stop / forever jobs
  * issue was: how to properly terminate a loop that has a infinite task going on
  * asynciojobs provides a nice replacement with forever jobs
  * this has been deployed almost everywhere, except for monitor for now
