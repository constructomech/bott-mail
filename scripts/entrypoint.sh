#!/usr/bin/env bash
# Container entrypoint.
#
# Runs as root to prepare the /data appdata layout and fix ownership, then:
#   - BRIDGE_MODE=login : run ONLY the interactive Bridge CLI (first-time login)
#   - default           : run supervisord (Bridge + bott-mail-service)
set -euo pipefail

DATA_USER=bott

# Ensure the appdata directory tree exists and is owned by the runtime user.
mkdir -p \
    /data/bridge \
    /data/config \
    /data/data \
    /data/data/plans \
    /data/logs
chown -R "${DATA_USER}:${DATA_USER}" /data

# Bridge keychain env (mirrors supervisor/bridge.conf so login mode matches).
export HOME=/data/bridge
export GNUPGHOME=/data/bridge/.gnupg
export PASSWORD_STORE_DIR=/data/bridge/.password-store
export XDG_CONFIG_HOME=/data/bridge/.config
export XDG_CACHE_HOME=/data/bridge/.cache
export XDG_DATA_HOME=/data/bridge/.local/share

if [ "${BRIDGE_MODE:-}" = "login" ]; then
    echo "[entrypoint] Starting Proton Bridge in interactive CLI mode for login."
    echo "[entrypoint] At the prompt run: login   then: info   then: exit"
    exec gosu "$DATA_USER" bash -lc '
        source /app/scripts/init-keychain.sh
        exec protonmail-bridge --cli
    '
fi

echo "[entrypoint] Starting supervisord (Bridge + bott-mail-service)."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
