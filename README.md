# Summary

`rhubarbe` is an `asyncio`/`python` module for loading images on a bunch of nodes using `frisbee`. It can be installed from `pypi` (see also `INSTALLING.md` for more details): 

    pip3 install rhubarbe

This is connected to an authorization system; most of the functions are rather intrusive, and for this reason they require the user to have obtained a lease (reservation), applicable to the current time and day, before the tool can be used. Note that the `root` user is unconditionnally granted permission.

## Image loading
To load your `fedora-23` image on 18 nodes simultaneously:
    
    rhubarbe load -i fedora-23 1-18 &
   
You can safely load another batch of nodes at the same time, maybe with a different bandwidth
   
    rhubarbe load -i ubuntu-16.04 -b 200 19-36 &

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
     
# Purpose

This is a tentative rewriting of the `omf6 load` and other similar commands, in python3 using `asyncio`. This results in a very efficient, single-thread, yet asynchroneous solution. The following features are currently available:

* `rhubarbe load` : parallel loading of an image, much like `omf6 load`. Two display modes are supported:
  * with the `-c` option running on top of curses to show individual progress for each node
  * otherwise, display is suitable to be redirected in a terminal with a global progressbar - much like `omf6 load`, or to a file.
 
  In any case, 'nextboot' symlinks (that tell `pxelinux` that a node to boot onto the frisbee image) are reliably removed in all cases, even if program crashes or is interrupted.
* `rhubarbe save` : image saving, much like `omf6 save`
* `rhubarbe wait` : waiting for all nodes to be available (i.e. to be connectable via ssh)

With these additional benefits:

* single configuration file in `/etc/rhubarbe/rhubarbe.conf`, individual setting can be overridden at either user- (`~/.rhubarbe.conf`) or directory- (`./rhubarbe.conf`) level
* all commands accept a timeout; a timeout that actually works, that is.
* all commands return a reliable code. When everything goes fine for all subject nodes they return 0, and 1 otherwise
* `load` comes with a `curses` mode that lets you visualize every node individually; very helpful when something goes wrong, so you can pinpoint which node is not behaving as expected.

A few additional features are available as well for convenience

* `rhubarbe leases` : inspect current leases
* `rhubarbe images` : list available images
* `rhubarbe inventory` : display inventory
* `rhubarbe config` : display config

Finally, `rhubarbe monitor` is a monitoring tool that can be used to feed a `socket.io` service about the current status of the testbed in realtime. This is what is called the *sidecar* service on R2Lab.

# How to use

## List of subcommands

The python entry point is named `rhubarbe` but it should be called with an additional subcommand.

    root@bemol ~ # rhubarbe
    Unknown subcommand help - use one among {nodes,status,on,off,reset,info,usrpstatus,usrpon,usrpoff,load,save,wait,monitor,leases,images,share,inventory,config,version}

	root@bemol ~ # rhubarbe load --help

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
    
## The `leases` subcommand

As of version 0.7.3:

* `rhubarbe leases` is offered to all users to inspect the current leases in the system
* `rhubarbe leases -i` is a utiliy offered to root only, unfortunately, that can create, update and delete leases in a simple terminal application.

## Logging

At this point:

* all logging goes into a file named `rhubarbe.log`, 
* except for the monitoring tool that logs into `/var/log/monitor.log`
 
# Installation

## Core

You need `python-3.4` or higher, and installation can be achieved simply with

    pip3 install rhubarbe
    
## Update

    pip3 install --upgrade rhubarbe


## Other libraries

The following will be automatically installed by `pip3` if not yet installed:

* `telnetlib3` for invoking `frisbee` on the nodes
* `aiohttp` for talking to the CMC cards
* `asyncssh` for talking to ssh (rhubarbe wait mostly for now); 
   * **ubuntu:** you may need to run `apt-get install libffi-dev` before `pip3 install asyncssh`
* `progressbar33` is used in rendering progress bar in the regular monitor (i.e. without the -c option).

# Setting up

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
 * `/etc/rhubarbe/rhubarbe.conf.local`
 * `~/.rhubarbe.conf`
 * `./rhubarbe.conf`

So in essence, there is 

 * a (mandatory) system-wide config `/etc/rhubarbe/rhubarbe.conf`, that ships with the `pip3` package and that contains all variable definitions; 
 * given that `/etc/rhubarbe/rhubarbe.conf` is likely to be overwritten at anytime by `pip3 install`, you can store your own system-wide changes in the (optional) file `/etc/rhubarbe.conf.local`;
 * then each user may override any value she likes,
 * and finally one can be even more specific and configure things at a directory level.

 The first file will come with all the settings defined, but any of the other 3 does not need to be complete and can just redefine one variable if needed.
 
Format is known as a `.ini` file, should be straightforward. Just beware to **not mention quotes** around strings, as such quotes end up in the python string verbatim.
 
## Authorization system

Among things to be configured is the URL for a leases server. This for now assumes the following

* You run an instance of an OMF_SFA service at that hostname and port
* And the OMF_SFA service exposes a single resource.

This is an admittedly specific policy for R2Lab, as opposed to other OMF-based deployments, since we want the reservations to be made on the testbed as a whole (to ensure reproducibility). This rather *ad hoc*  approach can easily be revisited if there's interest.

# A word on the `asyncio` module

As of September 2016, we only use the syntax of 3.5's asyncio, based on `async def` and `await`.

We use python 3.5's `asyncio` library. python3.5 can be taken as granted on the ubuntus we use on both `faraday` and `bemol`. This is consistent with python-3.5 being part of both Fedora-24 and Ubuntu-16.04

# TODO

* add option `rshare --clean` that would clean up the other/older candidates for a name
* some option in `rhubarbe leases` to skip the nightly leases

* in terms of the cleanup regarding display.stop / forever jobs
  * issue was: how to properly terminate a loop that has a infinite task going on
  * synciojobs provides a nice replacement with forever jobs
  * this has been deployed almost everywhere, except for monitor for now
