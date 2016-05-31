# 0.9.9 - 2016 May 31

* monitor to probe for /etc/rhubarbe-image an to expose last found name in
  the new 'imagename' field of its JSON output

# 0.9.8 - 2016 May 30

* images named in saving* can be referred to as their mere radical
* rsave accepts a --comment option for making /etc/rhubarbe-image
  more helpful

# 0.9.7 - 2016 May 19

* rhubarbe save now adds a line to /etc/rhubarbe-image that
  gives details on date, node, image name and unix account
* rhubarbe share now available. Permissions to use needs to
  be granted on an unix account basis in sudoers.
  On faraday this is done in `/etc/sudoers.d/rhubarbe-share`

# 0.9.6 - 2016 May 17

* bugfix for when saving an image triggers the global timeout
* ship experimental feature share
* forgetting -i in e.g. rload 12 ubuntu results in early failure

# 0.9.5 - 2016 May 11

* long overdue: on, off, reset, usrpon and usrpoff also require a reservation

# 0.9.4 - 2016 May 9

* support for usrpon, usrpoff and usrpstatus

# 0.9.3 - 2016 Apr 28

* can load local image without specifying abs. path

# 0.9.2 - 2016 Apr 27

* improvements in rhubarbe images: can filter on names, plus minor tweaks

# 0.9.1 - 2016 Apr 20

* add config flag user_auto_prefixes to locate users

# 0.9.0 - 2016 Mar 17

* reports all even wlans as wlan0 and odd as wlan1

# 0.8.9 - 2016 Mar 16

* bugfix - rhubarbe leases was broken

# 0.8.8 - 2016 Mar 8

* bugfix - when pingable but not ssh-able, we forgot to turn off control_ssh
  this in turn resurrects the mid-size green blog in livemap

# 0.8.7 - 2016 Mar 8

* tweaks timers so that the back-channel traffic can be handled
  other changes are made to liveleases so that we should get fewer
  such messages

# 0.8.6 - 2016 Mar 7

* ditto again

# 0.8.5 - 2016 Mar 7

* bugfixes - was badly broken

# 0.8.4 - 2016 Mar 4

* new subcommands on, off, reset and info
* new subcommand nodes, so in particular
* rhubarbe nodes -a allows to see the list of all nodes declared in rhubarbe.conf

# 0.8.3 - 2016 Feb 23

* monitor & synchroneous wait:
  * wait less (now a configurable amount, default 1ms)
  * but more often (each 'leases_step')

# 0.8.2 - 2016 Feb 23

* any message sent on the 'chan-leases-request' backchannel puts
  the lease-acquisition phase on fast-track

# 0.8.1 - 2016 Feb 16

* split the Leases class in two
  * new class OmfSfaProxy to be re-used in r2lab.inria.fr

# 0.8.0 - 2016 Feb 16

* an attempt to fix imperfect packaging

# 0.7.9 - 2016 Feb 15

* bugfix, method name change had not properly propagated 

# 0.7.8 - 2016 Feb 12

* more robust code for dealing with leases

# 0.7.7 - 2016 Feb 9

* monitor to also advertise to sidecar current leases on chan-leases
* new CHANGELOG.md
* new MANIFEST.in (for now only COPYING and README.md) 
