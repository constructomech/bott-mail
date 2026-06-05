#!/usr/bin/env bash
# Shared keychain bootstrap for headless Proton Bridge.
#
# Proton Bridge requires a keychain on Linux. In a headless container we use
# `pass` backed by a passphrase-less GPG key. This is idempotent: it only
# creates the key/store on first run. All state lives under $HOME (=/data/bridge)
# so it persists in the mounted appdata volume.
set -euo pipefail

: "${GNUPGHOME:=$HOME/.gnupg}"
: "${PASSWORD_STORE_DIR:=$HOME/.password-store}"

mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"

GPG_NAME="ProtonMail Bridge"

if ! gpg --list-secret-keys "$GPG_NAME" >/dev/null 2>&1; then
    echo "[keychain] Generating GPG key for Proton Bridge keychain..."
    gpg --batch --pinentry-mode loopback --passphrase '' \
        --quick-generate-key "$GPG_NAME" default default never
fi

if [ ! -f "$PASSWORD_STORE_DIR/.gpg-id" ]; then
    echo "[keychain] Initializing pass store..."
    KEY_FP=$(gpg --list-secret-keys --with-colons "$GPG_NAME" \
        | awk -F: '/^fpr:/ {print $10; exit}')
    pass init "$KEY_FP"
fi
