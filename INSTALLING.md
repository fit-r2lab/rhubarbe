# Requirements

## python

python 3.4 or higher is required

## libraries

All `pypi` requirements are to be handled automatically by `pip3`.

However, when trying to install `cffi`, `pip3` will need `Python.h`. If this happens to you please do either of the following

* on fedora: ```dnf install python3-devel libffi-devel```

* on ubuntu: ```apt-get install python3-dev libffi-dev```

## system setup

* this list grossly ignores the basic system setup 
* which actually is not trivial, as it involves dhcp / tftp and other pxelinux setup
* you can find rough [installation notes for fedora in INSTALLING-FEDORA.md](INSTALLING-FEDORA.md)

