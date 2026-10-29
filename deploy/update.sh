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
# Only the units that are installed AND enabled are restarted, so that
# this script also works on hosts that run none (or only some) of them, like
# distrait. The relaystemperatures service is run by its timer, so it is not
# restarted itself, it just picks up the new code next time; the timer is
# restarted like the others, if enabled.
#

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."

export PATH="/root/.local/bin:$PATH"

uv sync --frozen
systemctl daemon-reload

UNITS="monitornodes monitorphones monitorpdus accountsmanager relaysapi relaystemperatures.timer"

to_restart=""
skipped=""
for unit in $UNITS; do
    if systemctl is-enabled --quiet "$unit"; then
        to_restart="$to_restart $unit"
    else
        skipped="$skipped $unit"
    fi
done

[ -n "$skipped" ] && echo "update.sh: skipping (not installed or not enabled):$skipped"

if [ -n "$to_restart" ]; then
    systemctl restart $to_restart
else
    echo "update.sh: no enabled unit to restart"
fi
