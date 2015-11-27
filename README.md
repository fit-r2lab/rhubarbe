# Purpose

This is a tentative rewriting of the `omf6 load` and other similar commands in python3 using `asyncio`. This results in a single-thread, yet reactive, solution. The following features are currently available:

* `rhubarbe-load` : parallel loading of an image, much like `omf6 load`
  * Two modes are supported, with the `-c` option running on top of curses to show individual progress for each node
  * 'nextboot' symlinks (that tell a node to boot onto the frisbee image) are reliably removed in all cases, even if program crashes or is interrupted
* `rhubarbe-save` image saving, much like `omf6 save`
* `rhubarbe-wait` : waiting for all nodes to be available (can connect to ssh)

With these additional benefits:

* single configuration file in `/etc/rhubarbe/rhubarbe.conf`, individual setting can be overridden at either user- (`~/.rhubarbe.conf`) or directory- (`./rhubarbe.conf`) level
* all commands accept a timeout; a timeout that actually works, that is.
* all commands return a reliable code. When everything goes fine for all subject nodes they return 0, and 1 otherwise.

# How to use

## Invoking : node scope

The python entry point is named `rhubarbe-main.py` but it should be called through one of the symlinks like `rhubarbe-load`.

So in short:

    $ rhubarbe-load [-i filename] 1 4 5
    
The arguments, known as a *node_spec*`* would be similar to what `nodes` accepts as input, i.e.

    $ load-image 1-12 fit15-reboot18 ~4-6

Would essentially work on nodes 1 to 3, 7 to 12, and 15 to 18

Run `rhubarbe-load --help` as usual for a list of options.

## Env. variables    

If no node argument is provided on the command line, the value of the `NODES` environment variable is used. So 

    $ all-nodes
    $ focus-nodes-on
    $ echo this way you can check : NODES=$NODES
    $ rhubarbe-load

Would effectively address all nodes currently turned on

In addition, the `-a` option allows you to use the **ALL_NODES** variables. So to deal with all nodes except node 4, one can do

    $ rhubarbe-load -a ~4 
    

## Logging

At this point all logging goes into a file named `rhubarbe.log`
 
# Configuration

## Inventory

In short: see `/etc/rhubarbe/inventory.json`

Unfortunately the tool needs a mapping between hostnames and MAC addresses - essentially for messing with pxelinux *nextboot* symlinks. This is why the tool needs to find an inventory in a file named `/etc/rhubarbe/inventory.json`. 

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

Configuration is done through a collection of files, which are loaded in this order if they exist:

 * `/etc/rhubarbe/rhubarbe.conf`
 * `~/.rhubarbe.conf`
 * `./rhubarbe.conf`

 So in essence, there is a system-wide config (mandatory), that should contain all variable definitions, and possibly overridden values at a user level, or even more specific at a directory level; these 2 last files do not need to be complete and can just redefine one variable if needed.
 
 Format is like aim	 `.ini` file, should be straightforward. Just beware to **not mention quotes** around strings, as such quotes end up in the python string verbatim.

# Installation

## Core

Nothing has been done yet to provide a pypi packaging. As far as the code itself, everything is in `fitsophia/rhubarbe` and one can run the command from there, provided that the inventory and config are available in `/etc`. 


## Dependencies

## The `asyncio` module

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


## other libraries

Installed with `pip3`

* `telnetlib3` for invoking `frisbee` on the nodes
* `aiohttp` for talking to the CMC cards
* `asyncssh` for talking to ssh (rhubarbe-wait mostly for now); 
   * **ubuntu:** there is a need to run `apt-get install libffi-dev` before `pip3 install asyncssh`
* `progressbar33` is used in rendering progress bar in the regular monitor (i.e. without the -c option).

# TODO

## crucial (P1)

* couple usage with leases system
* packaging (pypi?)

## for deployment (P2)

* rewrite monitor.py within this framework ()
* the script for synchronizing images from bemol to faraday seems to have gone

## nice to have (P3)

* robustify ensure_reset ? (fit04)
  if a node is still answering ping right after it was reset then it's wrong

* should iwait have a telnet mode (-n/--telnet) as well ?

* refactor how mains are done; some have a monitor and some others not

## cosmetic (P4)

* remove need for $ALL_NODES and use config instead 
* nicer rhubarbe-list (sizes, symlinks, etc..)
* check if selector checks for nodes validity; i.e. `iload fedora-21` does not say that I screwed up and forgot the `-i`
* implement some way to store the logs from frisbee and imagezip somewhere
* wait really is not talkative; even without -v we'd expect some logging...
* is there a way to remove incomplete images under -save (both keybord interrupt and timeout..)
* should we not log all the messages on the feedback/bus onto logger as well ?