#!/bin/bash
#
# Apply the current checkout to the running services: sync the uv environment,
# reload the units and restart the services.
#
# Called by r2lab-embedded/services/run-updates.sh after it has moved this
# repo to the latest origin commit; can also be run by hand to force a redeploy.
# The git update itself is NOT done here, since resetting a repo under a
# running script is unsafe.
#
# Note on faraday there are TWO separate rhubarbe installations, on purpose:
#
# - the services (the units in this directory) run from this checkout,
#   in /opt/rhubarbe, using the uv environment in /opt/rhubarbe/.venv built
#   from uv.lock; this is the one that is maintained below, automatically;
# - the users' space, i.e. every `rhubarbe` command and `import rhubarbe` in
#   a regular session, still uses the system python and the pip package
#   in /usr/local; that one is NOT touched here, and is upgraded by hand with
#     pip install -U rhubarbe
#   after a release has been published on pypi
#
# so the services may run a more recent version than the one users see
# in between a push to main and the next release on pypi.
# also, a system upgrade of python needs a manual pip install -U rhubarbe
#
# The relaystemperatures service is run by its timer, and so it
# is not restarted here, it just picks up the new code next time.
#

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."

export PATH="$HOME/.local/bin:$PATH"

uv sync --frozen
systemctl daemon-reload
systemctl restart monitornodes monitorphones monitorpdus accountsmanager relaysapi
