#!/usr/bin/env bash
# Start Proton Bridge headless (no GUI). Binds IMAP/SMTP to 127.0.0.1 only
# inside this container. These ports are NEVER published.
set -euo pipefail

source /app/scripts/init-keychain.sh

echo "[bridge] Starting Proton Bridge (non-interactive)..."
# --noninteractive runs Bridge as a daemon with no TUI.
exec protonmail-bridge --noninteractive
