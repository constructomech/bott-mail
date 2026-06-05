"""Append-only audit logging.

Audit events are written as one JSON object per line. Never logs token
values, email bodies, raw HTML, or attachment contents.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditLog:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def record(self, *, actor: str, token_id: str, operation: str, **fields) -> None:
        event = {
            "ts": _now_iso(),
            "actor": actor,
            "token_id": token_id,
            "operation": operation,
        }
        # Defensive: never allow sensitive keys into the audit log.
        for forbidden in ("authorization", "token", "password", "body", "html"):
            fields.pop(forbidden, None)
        event.update(fields)
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
