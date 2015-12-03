# Summary

`rhubarbe` is an `asyncio`/`python` module for loading images on a bunch of nodes using `frisbee`. It can be installed from `pypi`: 

    pip3 install rhubarbe

This is connected to an authorization system; most of the functions are rather intrusive, and require the user to have obtained a lease (reservation), at the onelab portal, applicable to the current time and day, before the tool can be used. Note that the `root` user is unconditionnally granted permission.

To load your `fedora-21` image on 18 nodes simultaneously:
    
    rhubarbe load -i fedora-21 1-18 &
   
You can safely load another batch of nodes at the same time, maybe with a smaller bandwidth
   
    rhubarbe load -i ubuntu-1510 -b 200 19-36 &
    
To save the image of node 10, do this

    rhubarbe save 10


# Purpose

This is a tentative rewriting of the `omf6 load` and other similar commands, in python3 using `asyncio`. This results in a single-thread, yet reactive, solution. The following features are currently available:

* `rhubarbe load` : parallel loading of an image, much like `omf6 load`
  * Two modes are supported, with the `-c` option running on top of curses to show individual progress for each node
  * 'nextboot' symlinks (that tell a node to boot onto the frisbee image) are reliably removed in all cases, even if program crashes or is interrupted
* `rhubarbe save` : image saving, much like `omf6 save`
* `rhubarbe wait` : waiting for all nodes to be available (i.e. to be connectable via ssh)

With these additional benefits:

* single configuration file in `/etc/rhubarbe/rhubarbe.conf`, individual setting can be overridden at either user- (`~/.rhubarbe.conf`) or directory- (`./rhubarbe.conf`) level
* all commands accept a timeout; a timeout that actually works, that is.
* all commands return a reliable code. When everything goes fine for all subject nodes they return 0, and 1 otherwise.

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
    rhubarbe needs a subcommand in {load,save,status,wait,leases,images,inventory,config,monitor,version}

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
    


## Logging

At this point all logging goes into a file named `rhubarbe.log`, excpet for the monitoring tool that logs into `/var/log/monitor.log`
 
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
 * `/etc/rhubarbe/rhubarbe.conf.local`
 * `~/.rhubarbe.conf`
 * `./rhubarbe.conf`

So in essence, there is 
 * a (mandatory) system-wide config `/etc/rhubarbe/rhubarbe.conf`, hat contains all variable definitions; 
 * given that `/etc/rhubarbe/rhubarbe.conf` is likely to be overwritten at anytime by `pip3 install`, you can store your own system-wide changes in `/etc/rhubarbe.conf.local`;
 * then each user can override any value she likes,
 * and finally one can be even more specific and configure things at a directory level.

 The first file will come with all the settings defined, but any of the other 3 does not need to be complete and can just redefine one variable if needed.
 
Format is known as a `.ini` file, should be straightforward. Just beware to **not mention quotes** around strings, as such quotes end up in the python string verbatim.
 
## Authorization system

Among things to be configured is the URL for a leases server. This for now assumes the following

* You run an instance of an OMF_SFA service at that hostname and port
* And the OMF_SFA service exposes a single resource.

This is an admittedly specific policy for R2Lab, as opposed to other OMF-based deployments, since we want the reservations to be made on the testbed as a whole (to ensure reproducibility). This rather *ad hoc*  approach can easily be revisited if there's interest.

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

# A word on the `asyncio` module

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

* test. test. test
* test monitor.sh (monitor logs still in `rhubarbe.conf`...)
 
## for deployment (P2)

* fix issues with empty leases being displayed in the onelab portal
* take down previous monitor
* check that rhubarbe-save is connected to the authorization system
* some other commands (wait, status, etc...) should issue a warning when not authorized
* rhubarbe leases -l seems broken, and not sure if owners are properly displayed

## nice to have (P3)

* would be really cool if the authorization system could actually (propose to)  **get the lease** if nobody else uses the testbed at that time. 
  * need to do simple tests using the omf-generated certificate; will that alow to authenticate at the REST API and create the lease ?
  * in which case we'll most likely need to configure the resource name...

* robustify ensure_reset ? (fit04)
    if a node is still answering ping right after it was reset, then it is exhibiting the oblivion issue, so it needs to be turned off; maybe repeatedly so.

* *not even sure* refactor how mains are done; some have a monitor and some others not

* *not even sure* should wait have a telnet mode (-n/--telnet) as well ? 

## cosmetic - known bugs (P4)

* ~~save: might make sense to clean up saved image in case of keyboard interrupt or timeout - like, renaming the image as <>-broken~~
* ~~wait -v~~
* add another config file (for local tweaks)
* find a singleton class and use it for the_config, the_inventory, etc...
* implement some way to store the logs from frisbee and imagezip somewhere
* *not sure* should we not log all the messages on the feedback/bus onto logger as well ?
* *not quite useful* curses react to window resize
  * getch() to return curses.KEY_RESIZE in such a case
  * window.nodelay(1) allows to make getch() non-blocking
* status -1/--on : list nodes that are on - same for -0/--off; could apply to wait; with these options then the stdout could only contain the node names without any other sugar then
* rhubarbe config -> add an option to select a section
* monitor logs & one-liners as they were with earlier version

* *not even sure* do we need to tweak Node so that a control_hostname could be used as an argument to create a Node ? 

* test d3 plugin - message appears in the Console: it's odd because I cannot link this problem to this line number...
    
#
    TypeError: Cannot read property 'update_from_news' of undefined
    livetable.js:261