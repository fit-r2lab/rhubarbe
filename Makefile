# how to deploy in a test venv on preplab or production

help:
	echo "make prep to push on preplab"
	echo "make prod to push on production/faraday"

########## for deploying in a test venv
# use preplab as a test site by default
# unless phones or pdus or relays are needed for the test
deployment ?= prep

ifeq "$(deployment)" "prod"
    DEST=faraday.inria.fr
else
    DEST=preplab.pl.sophia.inria.fr
endif

# install in $(TMPDIR) for testing
TMPDIR=/tmp/r2lab-dev-rhubarbe
# the sandbox venv lives outside $(TMPDIR) so that rsync never touches it
VENV=/root/venvs/r2lab-dev
sync:
	@echo '===== '
	rsync -ai --relative $$(git ls-files) root@$(DEST):$(TMPDIR)/
	@echo '===== once copied, do the following as root on $(DEST)'
	@echo 'one-time, create the sandbox on the system python:'
	@echo '  uv venv $(VENV) --python /usr/bin/python3'
	@echo 'then install the versions pinned in uv.lock (editable) - first time,'
	@echo 'and whenever pyproject.toml or uv.lock changed:'
	@echo '  UV_PROJECT_ENVIRONMENT=$(VENV) uv sync --frozen --project $(TMPDIR)'
	@echo '===== '
	@echo 'and run with:'
	@echo '  source $(VENV)/bin/activate'

# shortcuts
prod r2lab faraday:
	$(MAKE) sync deployment=prod

prep preplab:
	$(MAKE) sync deployment=prep

.PHONY: sync prod r2lab faraday prep preplab


########## NOTE about actual install
# use deploy/update.sh

# check installed version
installed-version:
	apssh -t r2lab.infra rhubarbe version

.PHONY: installed-version
