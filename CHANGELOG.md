# 0.8.7 - 2016 Mar. 8

* tweaks timers so that the back-channel traffic can be handled
  other changes are made to liveleases so that we should get fewer
  such messages

# 0.8.6 - 2016 Mar. 7

* ditto again

# 0.8.5 - 2016 Mar. 7

* bugfixes - was badly broken

# 0.8.4 - 2016 Mar. 4

* new subcommands on, off, reset and info
* new subcommand nodes, so in particular
* rhubarbe nodes -a allows to see the list of all nodes declared in rhubarbe.conf

# 0.8.3 - 2016 Feb. 23

* monitor & synchroneous wait:
  * wait less (now a configurable amount, default 1ms)
  * but more often (each 'leases_step')

# 0.8.2 - 2016 Feb. 23

* any message sent on the 'chan-leases-request' backchannel puts
  the lease-acquisition phase on fast-track

# 0.8.1 - 2016 Feb. 16

* split the Leases class in two
  * new class OmfSfaProxy to be re-used in r2lab.inria.fr

# 0.8.0 - 2016 Feb. 16

* an attempt to fix imperfect packaging

# 0.7.9 - 2016 Feb. 15

* bugfix, method name change had not properly propagated 

# 0.7.8 - 2016 Feb. 12

* more robust code for dealing with leases

# 0.7.7 - 2016 Feb. 9

* monitor to also advertise to sidecar current leases on chan-leases
* new CHANGELOG.md
* new MANIFEST.in (for now only COPYING and README.md) 