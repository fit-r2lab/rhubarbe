########## uploading onto pypi
# depending on the value of USE_TWINE, we either do the upload with setup.py itself,
# or with the help of twine which apparently is the only way that mario could find out on his setup
define upload_pypi
$(if $(USE_TWINE),./setup.py sdist && twine upload dist/* -r $(1),./setup.py sdist upload -r $(1))
endef

########## for uploading onto pypi
# this assumes you have an entry 'pypi' in your .pypirc
# see pypi documentation on how to create .pypirc
LOCAL_ID=$(shell id)
ifneq "$(LOCAL_ID)" "$(filter $(LOCAL_ID),parmen)"
BUILD_ID=thierry
USE_TWINE=
else
BUILD_ID=mario
USE_TWINE=true
endif

PYPI_TARGET=pypi
PYPI_TARBALL_HOST=$(BUILD_ID)@build.onelab.eu
PYPI_TARBALL_TOPDIR=/build/rhubarbe

VERSION = $(shell python3 -c "from rhubarbe.version import version; print(version)")
VERSIONTAG = rhubarbe-$(VERSION)
VERSIONTAR = rhubarbe-$(VERSION).tar.gz
GIT-TAG-ALREADY-SET = $(shell git tag | grep '^$(VERSIONTAG)$$')
# to check for uncommitted changes
GIT-CHANGES = $(shell echo $$(git diff HEAD | wc -l))

# run this only once the sources are in on the right tag
pypi:
	@if [ $(GIT-CHANGES) != 0 ]; then echo "You have uncommitted changes - cannot publish"; false; fi
	@if [ -n "$(GIT-TAG-ALREADY-SET)" ] ; then echo "tag $(VERSIONTAG) already set"; false; fi
	@echo "You are about to release $(VERSION) - OK (Ctrl-c if not) ? " ; read _
	git tag $(VERSIONTAG)
	$(call upload_pypi,pypi)
	@if [ ssh $(PYPI_TARBALL_HOST) ls $(PYPI_TARBALL_TOPDIR)/$(VERSIONTAR) ] ;\
	  then echo "$(VERSIONTAR) already present on $(PYPI_TARBALL_HOST) - ignored" ;\
	  else rsync -av dist/$(VERSIONTAR) $(PYPI_TARBALL_HOST):$(PYPI_TARBALL_TOPDIR)/ ;\
	  fi

# it can be convenient to define a test entry, say testpypi, in your .pypirc
# that points at the testpypi public site
# no upload to build.onelab.eu is done in this case 
# try it out with
# pip install -i https://testpypi.python.org/pypi rhubarbe
# dependencies need to be managed manually though
testpypi: 
	$(call upload_pypi,testpypi)


##############################
tags:
	git ls-files | xargs etags

.PHONY: tags
############################## for deploying before packaging
# default is to mess with our preplab and let the production
# site do proper upgrades using pip3
deployment ?= bemol

ifeq "$(deployment)" "production"
    DEST=faraday.inria.fr
else
    DEST=bemol.pl.sophia.inria.fr
endif

# installing in ~/rhubarbe-sync

sync:
	@echo 'WARNING: need to run bin/rhubarbe from ~/rhubarbe-sync'
	rsync -av --relative $$(git ls-files) root@$(DEST):rhubarbe-sync/

both: bemol faraday

faraday:
	$(MAKE) sync deployment=production

bemol:
	$(MAKE) sync deployment=bemol

### old stuff
override:
	@echo WARNING : double check these locations on the remote end
	rsync -av $$(git ls-files rhubarbe) root@$(DEST):/usr/local/lib/python3.4/dist-packages/rhubarbe/
	rsync -av $$(git ls-files bin) root@$(DEST):/usr/local/bin
