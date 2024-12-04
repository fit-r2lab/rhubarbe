# to publish, use publish-to-pypi-from-pyproject

help:
	echo "make preplab to push on V0"
	echo "make r2lab to push on production"

##########
pyfiles:
	@git ls-files | grep '\.py$$'

pep8:
	$(MAKE) pyfiles | xargs flake8 --max-line-length=80 --exclude=__init__.py

pylint:
	$(MAKE) pyfiles | xargs pylint


.PHONY: pep8 pylint pyfiles
##############################
tags:
	git ls-files | xargs etags

.PHONY: tags

############################## for deploying before packaging
# default is to mess with our preplab and let the production
# site do proper upgrades using pip3
deployment ?= preplab

ifeq "$(deployment)" "production"
    DEST=faraday.inria.fr
else
    DEST=preplab.pl.sophia.inria.fr
endif

TMPDIR=/tmp/r2lab-dev-rhubarbe
# installing in $(TMPDIR) for testing
sync:
	@echo '===== '
	rsync -ai --relative $$(git ls-files) root@$(DEST):$(TMPDIR)/
	@echo '===== once copied, do the following as root on $(DEST)'
	@echo 'conda activate r2lab-dev-xxx && pip install -e $(TMPDIR)'

r2lab:
	$(MAKE) sync deployment=production

preplab:
	$(MAKE) sync deployment=preplab

.PHONY: sync faraday preplab

########## actually install
infra:
	apssh -t r2lab.infra pip3 install --upgrade rhubarbe
	ssh root@faraday.inria.fr systemctl restart monitornodes
	ssh root@faraday.inria.fr systemctl restart monitorphones
	ssh root@faraday.inria.fr systemctl restart monitorpdus
	ssh root@faraday.inria.fr systemctl restart accountsmanager

check:
	apssh -t r2lab.infra rhubarbe version

.PHONY: infra check
