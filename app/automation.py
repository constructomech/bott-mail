"""Dry-run automation recommendation validation and storage."""
from __future__ import annotations

import json
import glob
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any

from .config import SafetyConfig


_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "migrations"
)


ALLOWED_ACTIONS = {"archive", "unsubscribe", "add_tag", "slack_notify"}
KNOWN_CLASSIFICATIONS = {"political", "not_political", "travel", "deadline", "school", "uncertain", "other"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    rejected_reasons: list[str]


def validate_recommendation(
    *,
    message_id: str,
    recommendation_message_id: str,
    classification: str,
    confidence: float,
    actions: list[dict[str, Any]],
    safety: SafetyConfig,
) -> ValidationResult:
    reasons: list[str] = []

    if recommendation_message_id != message_id:
        reasons.append("recommendation message_id does not match request path")

    if classification not in KNOWN_CLASSIFICATIONS:
        reasons.append("unknown classification")

    if confidence < 0 or confidence > 1:
        reasons.append("confidence must be between 0 and 1")

    if len(actions) > 4:
        reasons.append("too many actions")

    for action in actions:
        action_type = str(action.get("type", ""))
        if action_type not in ALLOWED_ACTIONS:
            reasons.append(f"unknown action: {action_type or '<missing>'}")
            continue
        if action_type == "archive" and not safety.allow_archive:
            reasons.append("archive action disabled by safety.allow_archive=false")
        if action_type == "add_tag" and not safety.allow_label:
            reasons.append("add_tag action disabled by safety.allow_label=false")
        if action_type == "unsubscribe":
            mechanism = action.get("mechanism")
            if mechanism not in (None, "list_unsubscribe"):
                reasons.append("unsubscribe only supports list_unsubscribe mechanism")

    return ValidationResult(accepted=not reasons, rejected_reasons=reasons)


class AutomationStore:
    def __init__(self, sqlite_path: str, account: str = "default") -> None:
        self._path = sqlite_path
        self._account = account
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            for path in sorted(glob.glob(os.path.join(_MIGRATIONS_DIR, "*.sql"))):
                with open(path) as f:
                    conn.executescript(f.read())

    def record_decision(
        self,
        *,
        message_id: str,
        rule_name: str | None,
        classification: str,
        confidence: float,
        actions: list[dict[str, Any]],
        raw_recommendation: dict[str, Any],
        validation: ValidationResult,
    ) -> dict[str, Any]:
        decision_id = str(uuid.uuid4())
        created_at = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO automation_decisions (
                  id, account, message_id, rule_name, classification,
                  confidence, accepted, rejected_reasons, actions_json,
                  raw_recommendation_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    decision_id,
                    self._account,
                    message_id,
                    rule_name,
                    classification,
                    confidence,
                    1 if validation.accepted else 0,
                    json.dumps(validation.rejected_reasons),
                    json.dumps(actions),
                    json.dumps(raw_recommendation),
                    created_at,
                ),
            )
        return {
            "decision_id": decision_id,
            "message_id": message_id,
            "accepted": validation.accepted,
            "rejected_reasons": validation.rejected_reasons,
            "dry_run": True,
            "created_at": created_at,
        }

    def record_execution(
        self,
        *,
        decision_id: str,
        batch_id: str,
        request_id: str,
        message_id: str,
        action_type: str,
        status: str,
        detail: str | None = None,
    ) -> dict[str, Any]:
        execution_id = str(uuid.uuid4())
        created_at = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO automation_executions (
                  id, account, decision_id, batch_id, request_id, message_id,
                  action_type, status, detail, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    execution_id,
                    self._account,
                    decision_id,
                    batch_id,
                    request_id,
                    message_id,
                    action_type,
                    status,
                    detail,
                    created_at,
                ),
            )
        return {
            "execution_id": execution_id,
            "decision_id": decision_id,
            "message_id": message_id,
            "action_type": action_type,
            "status": status,
            "detail": detail,
            "created_at": created_at,
        }

    def create_classification_batch(
        self,
        *,
        trigger: str,
        messages: list[dict[str, Any]],
        ttl_minutes: int = 30,
    ) -> dict[str, Any]:
        batch_id = f"batch_{uuid.uuid4().hex}"
        created_at = _now_iso()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: list[dict[str, Any]] = []
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO classification_batches (
                  id, account, status, trigger, created_at, expires_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (batch_id, self._account, "pending", trigger, created_at, expires_at),
            )
            for message in messages:
                request_id = f"req_{uuid.uuid4().hex}"
                body_sha = str(message.get("body_sha256") or "")
                conn.execute(
                    """
                    INSERT INTO classification_batch_items (
                      request_id, batch_id, account, message_id, body_sha256,
                      status, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        request_id,
                        batch_id,
                        self._account,
                        message["id"],
                        body_sha,
                        "pending",
                        created_at,
                    ),
                )
                items.append(
                    {
                        "request_id": request_id,
                        "message_id": message["id"],
                        "body_sha256": body_sha,
                    }
                )
        return {
            "batch_id": batch_id,
            "trigger": trigger,
            "created_at": created_at,
            "expires_at": expires_at,
            "items": items,
        }

    def get_pending_batch_items(self, batch_id: str) -> dict[str, dict[str, Any]]:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            batch = conn.execute(
                """
                SELECT * FROM classification_batches
                WHERE id = ? AND account = ? AND status = 'pending' AND expires_at >= ?
                """,
                (batch_id, self._account, now),
            ).fetchone()
            if batch is None:
                return {}
            rows = conn.execute(
                """
                SELECT * FROM classification_batch_items
                WHERE batch_id = ? AND account = ? AND status = 'pending'
                """,
                (batch_id, self._account),
            ).fetchall()
        return {
            row["request_id"]: {
                "request_id": row["request_id"],
                "message_id": row["message_id"],
                "body_sha256": row["body_sha256"],
            }
            for row in rows
        }

    def complete_batch_items(self, batch_id: str, request_ids: list[str]) -> None:
        if not request_ids:
            return
        completed_at = _now_iso()
        with self._lock, self._connect() as conn:
            for request_id in request_ids:
                conn.execute(
                    """
                    UPDATE classification_batch_items
                    SET status = 'completed', completed_at = ?
                    WHERE batch_id = ? AND request_id = ? AND account = ?
                    """,
                    (completed_at, batch_id, request_id, self._account),
                )
            remaining = conn.execute(
                """
                SELECT COUNT(*) FROM classification_batch_items
                WHERE batch_id = ? AND account = ? AND status = 'pending'
                """,
                (batch_id, self._account),
            ).fetchone()[0]
            if remaining == 0:
                conn.execute(
                    """
                    UPDATE classification_batches
                    SET status = 'completed', completed_at = ?
                    WHERE id = ? AND account = ?
                    """,
                    (completed_at, batch_id, self._account),
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
