#!/usr/bin/env sh
# RitiBackup entrypoint.
#
# The application reads its configuration directly from /data/options.json
# (written by the Supervisor), so bashio is not required. We simply move into
# the app directory and hand off to the Python module. `exec` replaces the
# shell so signals (SIGTERM on addon stop) reach Python cleanly.
set -e

cd /opt/ritibackup

# The Supervisor passes SUPERVISOR_TOKEN as a container environment variable,
# which is normally inherited here. As a safety net (in case s6-overlay scrubbed
# the environment for the CMD), recover it from the s6 container_environment if
# it isn't already set, so the app can always reach the Supervisor API.
for _envdir in /run/s6/container_environment /var/run/s6/container_environment; do
    if [ -z "${SUPERVISOR_TOKEN:-}" ] && [ -r "${_envdir}/SUPERVISOR_TOKEN" ]; then
        SUPERVISOR_TOKEN="$(cat "${_envdir}/SUPERVISOR_TOKEN")"
        export SUPERVISOR_TOKEN
    fi
done

# Surface the chosen log level to the app via env as a convenience; the app
# also reads it from options.json.
if [ -f /data/options.json ]; then
    LEVEL="$(python3 - <<'PY' 2>/dev/null || true
import json
try:
    with open("/data/options.json") as f:
        print(json.load(f).get("log_level", "info"))
except Exception:
    print("info")
PY
)"
    export RITI_LOG_LEVEL="${LEVEL:-info}"
fi

echo "[RitiBackup] starting (log level: ${RITI_LOG_LEVEL:-info})"
exec python3 -m app
