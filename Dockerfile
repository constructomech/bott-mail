# bott-mail — combined container: Proton Bridge (headless) + bott-mail-service
#
# Single product-style container. Proton Bridge binds IMAP/SMTP to localhost
# only; the bridge ports are NEVER published. Only the service API (8080) is
# exposed. NOTE: amd64 image (the official Proton Bridge .deb is amd64).
FROM python:3.12-slim

# --- Proton Bridge version (override at build: --build-arg BRIDGE_VERSION=3.x.y) ---
ARG BRIDGE_VERSION=3.22.0
ARG BRIDGE_DEB_URL=https://proton.me/download/bridge/protonmail-bridge_${BRIDGE_VERSION}-1_amd64.deb

WORKDIR /app

# System dependencies:
#   - Proton Bridge runtime libs: libsecret-1-0, libfido2-1
#   - Keychain backend: pass, gpg
#   - Process supervision: supervisor
#   - Privilege drop: gosu
#   - misc: wget, ca-certificates, procps
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget ca-certificates gnupg pass supervisor gosu procps \
        libsecret-1-0 libfido2-1 socat \
    && wget -O /tmp/protonmail-bridge.deb "${BRIDGE_DEB_URL}" \
    && apt-get install -y --no-install-recommends /tmp/protonmail-bridge.deb \
    && rm -f /tmp/protonmail-bridge.deb \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user (owns /data and runs Bridge + service).
RUN useradd --create-home --uid 1000 --shell /bin/bash bott

# Python dependencies for the service.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code, migrations, scripts, supervisor configs.
COPY app/ app/
COPY migrations/ migrations/
COPY scripts/ scripts/
RUN chmod +x scripts/*.sh

COPY supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY supervisor/bridge.conf /etc/supervisor/conf.d/bridge.conf
COPY supervisor/service.conf /etc/supervisor/conf.d/service.conf

# Appdata volume (Bridge state, SQLite index, audit log, logs).
VOLUME /data

# Only the service API is exposed. Bridge IMAP/SMTP stay on localhost.
EXPOSE 8080

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
