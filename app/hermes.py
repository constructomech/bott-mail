"""Outbound Hermes webhook client for mail classification requests."""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
from dataclasses import dataclass
from typing import Any


class HermesWebhookError(Exception):
    """Raised when a Hermes webhook request cannot be sent successfully."""


@dataclass(frozen=True)
class HermesWebhookConfig:
    url: str
    secret: str
    timeout_seconds: float = 10.0


def build_classification_payload(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": "mail.classify",
        "message": {
            "id": message["id"],
            "folder": message["folder"],
            "from": message.get("from"),
            "to": message.get("to", []),
            "cc": message.get("cc", []),
            "subject": message.get("subject"),
            "date": message.get("date"),
            "text": message.get("text") or "",
            "unread": bool(message.get("unread", False)),
        },
    }


def encode_signed_json(payload: dict[str, Any], secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return body, signature


class HermesWebhookClient:
    def __init__(self, config: HermesWebhookConfig) -> None:
        self._config = config

    def send_classification_request(self, message: dict[str, Any]) -> int:
        if not self._config.url:
            raise HermesWebhookError("Hermes webhook URL is not configured")
        if not self._config.secret:
            raise HermesWebhookError("Hermes webhook secret is not configured")

        payload = build_classification_payload(message)
        body, signature = encode_signed_json(payload, self._config.secret)
        req = urllib.request.Request(
            self._config.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "mail.classify",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self._config.timeout_seconds
            ) as resp:
                return int(resp.status)
        except Exception as exc:  # noqa: BLE001
            raise HermesWebhookError(str(exc)) from exc
