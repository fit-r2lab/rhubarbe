include Makefile.pypi

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

# installing in /tmp/rhubarbe-sync for testing
sync:
	@echo 'export PYTHONPATH=/tmp/rhubarbe-sync; alias rhu=/tmp/rhubarbe-sync/rhubarbe/__main__.py'
	rsync -av --relative $$(git ls-files) root@$(DEST):/tmp/rhubarbe-sync/

faraday:
	$(MAKE) sync deployment=production

preplab:
	$(MAKE) sync deployment=preplab

.PHONY: sync faraday preplab

########## actually install
infra:
	apssh -t r2lab.infra pip3 install --upgrade rhubarbe
	ssh root@faraday.inria.fr systemctl restart monitornodes
	ssh root@faraday.inria.fr systemctl restart monitorphones
	ssh root@faraday.inria.fr systemctl restart accountsmanager
check:
	apssh -t r2lab.infra rhubarbe version

.PHONY: infra check
