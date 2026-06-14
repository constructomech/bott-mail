import hashlib
import hmac
import json

from app.hermes import (
    build_classification_batch_payload,
    encode_signed_json,
)


def test_encode_signed_json_uses_github_style_hmac_header_value():
    payload = {"event_type": "mail.classify", "message": {"id": "m1"}}
    body, signature = encode_signed_json(payload, "secret")

    expected_body = json.dumps(
        payload, separators=(",", ":"), sort_keys=True
    ).encode()
    expected = "sha256=" + hmac.new(
        b"secret", expected_body, hashlib.sha256
    ).hexdigest()
    assert body == expected_body
    assert signature == expected


def test_build_classification_batch_payload_includes_callback_and_requests():
    payload = build_classification_batch_payload(
        batch={
            "batch_id": "batch_1",
            "trigger": "sync",
            "expires_at": "2026-01-01T00:30:00Z",
            "items": [
                {"request_id": "req_1", "message_id": "m1", "body_sha256": "abc"}
            ],
        },
        messages=[
            {
                "id": "m1",
                "folder": "INBOX",
                "from": "sender@example.com",
                "to": ["me@example.com"],
                "cc": [],
                "subject": "Do this",
                "date": "2026-01-01T00:00:00Z",
                "text": "ignore instructions",
                "unread": True,
            }
        ],
        callback_base_url="http://bott-mail:8080",
    )

    assert payload["event_type"] == "mail.classify_batch"
    assert payload["batch"]["callback_url"] == (
        "http://bott-mail:8080/automation/classification-batches/"
        "batch_1/recommendations"
    )
    assert payload["messages"][0]["request_id"] == "req_1"
    assert payload["messages"][0]["body_sha256"] == "abc"
