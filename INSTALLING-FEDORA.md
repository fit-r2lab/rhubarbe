# scope

This document tries to summarize the steps needed to do a full install of rhubarbe on fedora.

* 9 March 2017
* fedora25
* based on reinstall of etourdi/r0lab/preplab
* was done after the facts so may not be 100% accurate
* previous install of bemol was with ubuntu

# base

* 3 network interfaces
  * `internet`
  * `reboot` on 192.168.1.100
  * `control` on 192.168.3.100
  * renamed with udev rules

* **turned OFF selinux** f...
* **turned off firewalld**
  * would ruin DHCP traffic
  * need to use iptables instead
  * or to better understand firewalld

# images

* restored `/var/lib/rhubarbe-images` from bemol's backup

# dnsmasq

* `dnf install dnsmasq`
* `systemctl enable dnsmasq`
* `systemctl start dnsmasq`

* `/etc/dnsmasq.conf`
* plus of course the outcome of running `make preplab` in `r2lab/inventory`

```
conf-dir=/etc/dnsmasq.d,.rpmnew,.rpmsave,.rpmorig


log-facility=/var/log/dnsmasqfit.log
log-dhcp
tftp-root=/tftpboot

dhcp-option=option:ntp-server,138.96.0.33
dhcp-option=option:ntp-server,138.96.0.34
dhcp-option=option:ntp-server,138.96.0.35
dhcp-option=option:ntp-server,138.96.0.36

dhcp-boot=pxelinux.0

interface=control
listen-address=192.168.3.100
dhcp-range=control,192.168.3.250,192.168.3.251,255.255.255.0,1h
dhcp-authoritative
enable-tftp
```

# syslinux/tftpboot

* `dnf install syslinux-tftpboot`

# frisbeed

* manually installed `/usr/sbin/frisbeed`
* from [the binary in the r2lab repo](https://github.com/parmentelat/r2lab/blob/public/frisbee-binaries-inria/frisbeed) 
  * that incidentally would be a better fit in `rhubarbe` itself
