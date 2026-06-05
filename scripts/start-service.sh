#!/usr/bin/env bash
# Start bott-mail-service (FastAPI via uvicorn).
set -euo pipefail

cd /app

HOST="${BOTT_MAIL_HOST:-0.0.0.0}"
PORT="${BOTT_MAIL_PORT:-8080}"

echo "[service] Starting bott-mail-service on ${HOST}:${PORT}..."
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
