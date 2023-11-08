# INSTALLATION NOTES

some notes taken when rebuilding `distrait.pl.sophia.inria.fr`

## general

- date : 2023 Nov 7
- fedora : 38
- major changes from faraday:
  - the network config is made in nmcli natively - and not in ifcfg files
  - as a result dnsmasq MUST NOT be setup as a standalone app
  - but must be configured as a NetworkManager plugin
  - see https://docs.fedoraproject.org/en-US/fedora-server/administration/dnsmasq/
    for more details
  - at this point the testbed config still goes in /etc/dnsmasq.d/testbed.conf
    but is symlinked from /etc/NetworkManager/dnsmasq.d/


## base install

- used a fedora38-server vanilla distro
- usual bootstrap with ssh keys
- [ ] cd /; git init; echo '*' > .gitignore; git add -f .gitignore; git commit -m"gitignore"
- did network config manually with nmcli
  MAKE SURE TO HAVE THE .nmconection file in mode 600
- [ ] nmcli con up control


## installed rpms

- [ ] dnf update
- [ ] dnf install emacs-nox
- [ ] dnf-install iptables iptables-services
- [ ] rpm -e firewalld
- [ ] dnf install python python3-pip
- [ ] dnf install -y git
- [ ] turn off selinux
- [ ] reboot

## images

- [ ] for testing rhubarbe load the best is
  ubuntu-16.04-v3-stamped.ndz
  because it's only 600 Mb
- later on I went for getting the images built within 300 days.. why not
  ```
  root@faraday
  rsync -aq -partial $(find /var/lib/rhubarbe-images -maxdepth 1 -type f -mtime -300) root@distrait.pl.sophia.inria.fr:/var/lib/rhubarbe-images/
  ```

## post install

- [ ] clone r2lab-embedded and diana
- [ ] install diana bash
- [ ] get-gitprompt
- [ ] pip install rhubarbe
- [ ] apply r2lab-misc/inventory to create config
- [ ] symlink /etc/profile.d/ faraday.sh and r2labutils.sh
- [ ] installed frisbee binaries from r2lab-misc/ (manually)
- [ ] dnf install syslinux syslinux-nonlinux syslinux-tftpboot
- [ ] restore /tftpboot from faraday
- [ ] in rhubarbe.conf.local:
  ```
  [nodes]
  load_default_timeout = 900
  save_default_timeout = 900
  [networking]
  bandwidth.distrait = 200
  ```
- [ ] install r2lab-nightly timer from r2lab-embedded
  ```
  systemctl start r2lab-nightly.timer
  systemctl stop r2lab-nightly.timer
  ```
- [ ] installed miniconda for setting up a dev env (see make preplab in rhubarbe)

- to be continued...
  probably need to tweak this code for the cases where there's no inventory-pdus.yaml
  but the nodes do get turned off, so..


########
# historical section - OLDY (2017)

## context

the context for the contents of this file was:

* 9 March 2017
* fedora25
* based on reinstall of r0lab/preplab  (known as etourdi.pl.sophia.inria.fr at the time)
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

# netcat

## on ubuntu

* we run this

```
nc -d -l 192.168.3.100 10001
```

* where `-d` means 'do not read from stdin'

## on fedora

* the `nmap-ncat` package has a binary
* where `-d` expects an argument that expresses a delay

## config

so we now have a new config flag
[frisbee]
netcat_style = ubuntu

that needs to be set to fedora in `/etc/rhubarbe/rhubarbe.conf.local`
