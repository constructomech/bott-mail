"""Dry-run automation recommendation validation and storage."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import SafetyConfig


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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
