# Summary

`rhubarbe` is an `asyncio`/`python` module for loading images on a bunch of nodes using `frisbee`. It can be installed from `pypi`: 

    pip3 install rhubarbe

To load your `fedora-21` image on 18 nodes simultaneously:
    
    rhubarbe load -i fedora-21 1-18 &
   
You can safely load another batch of nodes at the same time, maybe with a smaller bandwidth
   
    rhubarbe load -i ubuntu-1510 -b 200 19-36 &
    
To save the image of node 10, do this

    rhubarbe save 10

This is connected to an authorization system; most of the functions are rather intrusive, and require the user to have obtained a lease (reservation), at the onelab portal, applicable to the current time and day, before the tool can be used. Note that the `root` user is unconditionnally granted permission.


# Purpose

This is a tentative rewriting of the `omf6 load` and other similar commands, in python3 using `asyncio`. This results in a single-thread, yet reactive, solution. The following features are currently available:

* `rhubarbe load` : parallel loading of an image, much like `omf6 load`
  * Two modes are supported, with the `-c` option running on top of curses to show individual progress for each node
  * 'nextboot' symlinks (that tell a node to boot onto the frisbee image) are reliably removed in all cases, even if program crashes or is interrupted
* `rhubarbe save` image saving, much like `omf6 save`
* `rhubarbe wait` : waiting for all nodes to be available (can connect to ssh)

With these additional benefits:

* single configuration file in `/etc/rhubarbe/rhubarbe.conf`, individual setting can be overridden at either user- (`~/.rhubarbe.conf`) or directory- (`./rhubarbe.conf`) level
* all commands accept a timeout; a timeout that actually works, that is.
* all commands return a reliable code. When everything goes fine for all subject nodes they return 0, and 1 otherwise.

# How to use

## List of subcommands

The python entry point is named `rhubarbe` but it should be called with an additional subcommand.

    root@bemol ~ # rhubarbe
    Need to run rhubarbe with a subcommand among {load,save,status,wait,list,version}

	root@bemol ~ # rhubarbe load --help

## Invoking : node scope

So in short:

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
  * 1 through 37 on `faraday.inria.fr`
* negation
  * `$ rhubarbe load -a ~10-20` on nodes
  * 1 to 9, and 21 to 37

Run `rhubarbe load --help` as usual for a list of options.

## Env. variables    

If no node argument is provided on the command line, the value of the `NODES` environment variable is used. So 

    $ all-nodes
    $ focus-nodes-on
    $ echo this way you can check : NODES=$NODES
    $ rhubarbe load

Would effectively address all nodes currently turned on

In addition, the `-a` option allows you to refer to the whole testbed (see `testbed.all_scope` in the config). It can be used and combined with other ranges, so to deal with all nodes except node 4, one can do

    $ rhubarbe load -a ~4 
    

## Logging

At this point all logging goes into a file named `rhubarbe.log`
 
# Configuration

## Inventory

In short: see `/etc/rhubarbe/inventory.json`

Unfortunately the tool needs a mapping between hostnames and MAC addresses - essentially for messing with pxelinux *nextboot* symlinks. This is why the tool needs to find an inventory in a file named `/etc/rhubarbe/inventory.json`. See an extract below; note that the `'data'` entry is not needed by the tools, we have them in place over here at r2lab for convenience only. The CMC mac address is not needed either, strictly speaking, as of this writing.

**On R2LAB**: this is taken care of by `inventory/configure.py` and its `Makefile`. Note that like for the OMF JSON inventory file, `configure.py` creates 2 different files for faraday and bemol - to account for any replacement node on faraday, like when e.g. node 41 actually sits in slot 15.

FYI an inventory files just looks like below; the `data` field is not needed

#
    # less /etc/rhubarbe/inventory.json
     [
      {
        "cmc": {
          "hostname": "reboot01",
          "ip": "192.168.1.1",
          "mac": "02:00:00:00:00:01"
        },
        "control": {
          "hostname": "fit01",
          "ip": "192.168.3.1",
          "mac": "00:03:1d:0e:03:19"
        },
        "data": {
          "hostname": "data01",
          "ip": "192.168.2.1",
          "mac": "00:03:1d:0e:03:18"
        }
      },
      ... etc
      ]

## Configuration

In short: see `/etc/rhubarbe/rhubarbe.conf`

Configuration is done through a collection of files, which are loaded in this order if they exist:

 * `/etc/rhubarbe/rhubarbe.conf`
 * `~/.rhubarbe.conf`
 * `./rhubarbe.conf`

 So in essence, there is a system-wide config (mandatory), that should contain all variable definitions, and possibly overridden values at a user level, or even more specific at a directory level; these 2 last files do not need to be complete and can just redefine one variable if needed.
 
 Format is like aim	 `.ini` file, should be straightforward. Just beware to **not mention quotes** around strings, as such quotes end up in the python string verbatim.
 
## Permission system

Among things to be configured is the URL for a leases server. This for now assumes the following

* You run an instance of an OMF_SFA service at that hostname and port
* And the OMF_SFA service exposes a single resource.

This is an admittedly specific policy for R2Lab, as opposed to other OMF-based deployments, since we want the reservations to be made on the testbed as a whole, since this is not sharable. This rather *ad hoc*  approach can easily be revisited if there's interest.

# Installation

## Core

You need `python-3.4` or higher. A `pypi` packaging is in the works. 

    pip3 install rhubarbe

## Other libraries

Installed with `pip3`

* `telnetlib3` for invoking `frisbee` on the nodes
* `aiohttp` for talking to the CMC cards
* `asyncssh` for talking to ssh (rhubarbe wait mostly for now); 
   * **ubuntu:** there is a need to run `apt-get install libffi-dev` before `pip3 install asyncssh`
* `progressbar33` is used in rendering progress bar in the regular monitor (i.e. without the -c option).

## A word on the `asyncio` module

We use python 3.4's `asyncio` library. python3.4 can be taken as granted on the ubuntus we use on both `faraday` and `bemol`. 

**Note** the syntax for writing asynchroneous code has changed in 3.5 and now relies on `async` and `await`. So it would have been nice to assume `python3.5` instead of `3.4`. However as of this writing (Nov. 2015), python3.5 is not yet available on ubuntu-LTS-14.04 that we use, and I'd rather not install that from sources.

In practical terms this means that whenever we use 

    # python-3.4 syntax (the old one)
    @asyncio.coroutine
    def foo():
        yield from bar()
    
we would have written instead in pure python-3.5 this

    # new syntax since python-3.5
    async def foo():
        await bar()


# TODO

## P1 : known bugs for production

* test. test. test:
 
## for deployment (P2)

* rewrite monitor.py within this framework ()
* the script for synchronizing images from bemol to faraday seems to have gone

## nice to have (P3)

* robustify ensure_reset ? (fit04)
    if a node is still answering ping right after it was reset, then it is exhibiting the oblivion issue, so it needs to be turned off; maybe repeatedly so.

* *not even sure* refactor how mains are done; some have a monitor and some others not

* *not even sure* should iwait have a telnet mode (-n/--telnet) as well ? 

* would be really cool if the authorization system could actually **get the lease** if nobody else uses the testbed at that time. 
  * need to do simple tests using the omf-generated certificate; will that alow to authenticate at the REST API and create the lease ?
  * in which case we'll most likely need to configure the resource name...

## cosmetic - known bugs (P4)

* save: might make sense to clean up saved image in case of keyboard interrupt or timeout
* implement some way to store the logs from frisbee and imagezip somewhere
* wait really is not talkative; even without -v we'd expect some logging...
* is there a way to remove incomplete images under -save (both keybord interrupt and timeout..)
* should we not log all the messages on the feedback/bus onto logger as well ?
job but somehow something later does it again...
* curses react to window resize
  * getch() to return curses.KEY_RESIZE in such a case
  * window.nodelay(1) allows to make getch() non-blocking
* Istatus -1/--on : list nodes that are on - same for -0/--off
