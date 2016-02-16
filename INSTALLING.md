# Requirements

## python

python 3.4 or higher is required

## libraries

All `pypi` requirements are to be handled automatically by `pip3`.

However, when trying to install `cffi`, `pip3` will need `Python.h`. If this happens to you please do either of the following

* on fedora: ```dnf install python3-devel libffi-devel```

* on ubuntu: ```apt-get install python3-dev libffi-dev```


