import importlib
import sys

from fastapi.testclient import TestClient

from app.automation import validate_recommendation
from app.config import SafetyConfig
from app.index import MessageIndex, stable_id
from app.mail_parse import parse_email


def test_validate_recommendation_accepts_allowed_dry_run():
    result = validate_recommendation(
        message_id="m1",
        recommendation_message_id="m1",
        classification="political",
        confidence=0.91,
        actions=[{"type": "archive"}, {"type": "unsubscribe", "mechanism": "list_unsubscribe"}],
        safety=SafetyConfig(allow_archive=True),
    )

    assert result.accepted is True
    assert result.rejected_reasons == []


def test_validate_recommendation_rejects_bad_actions():
    result = validate_recommendation(
        message_id="m1",
        recommendation_message_id="m2",
        classification="unknown",
        confidence=1.2,
        actions=[{"type": "delete"}, {"type": "archive"}],
        safety=SafetyConfig(allow_archive=False),
    )

    assert result.accepted is False
    assert "recommendation message_id does not match request path" in result.rejected_reasons
    assert "unknown classification" in result.rejected_reasons
    assert "confidence must be between 0 and 1" in result.rejected_reasons
    assert "unknown action: delete" in result.rejected_reasons
    assert "archive action disabled by safety.allow_archive=false" in result.rejected_reasons


def _load_app(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
auth:
  tokens:
    - id: hermes-read
      token_env: BOTT_MAIL_READ_TOKEN
      scopes:
        - messages:read
        - messages:search
    - id: hermes-automation
      token_env: BOTT_MAIL_AUTOMATION_TOKEN
      scopes:
        - automation:recommend
        - automation:classify
hermes:
  webhook_url: http://hermes.example/webhooks/mail-classify
  webhook_secret_env: BOTT_MAIL_HERMES_WEBHOOK_SECRET
storage:
  sqlite_path: {tmp_path / "mail.sqlite"}
  audit_log: {tmp_path / "audit.log"}
safety:
  allow_archive: true
automation:
  auto_classify_new_mail: true
  auto_classify_limit_per_sync: 10
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOTT_MAIL_CONFIG", str(config))
    monkeypatch.setenv("BOTT_MAIL_READ_TOKEN", "read-token")
    monkeypatch.setenv("BOTT_MAIL_AUTOMATION_TOKEN", "automation-token")
    monkeypatch.setenv("BOTT_MAIL_HERMES_WEBHOOK_SECRET", "webhook-secret")

    sys.modules.pop("app.main", None)
    module = importlib.import_module("app.main")
    return module.app, str(tmp_path / "mail.sqlite")


def test_record_recommendation_endpoint_accepts_and_stores_dry_run(
    monkeypatch, tmp_path, sample_eml
):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")
    client = TestClient(app)

    resp = client.post(
        f"/automation/messages/{mid}/recommendation",
        headers={"Authorization": "Bearer automation-token"},
        json={
            "message_id": mid,
            "rule_name": "political-mail",
            "classification": "political",
            "confidence": 0.95,
            "actions": [{"type": "archive"}],
            "reason": "Campaign email",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["dry_run"] is True
    assert body["message_id"] == mid
    assert body["decision_id"]


def test_record_recommendation_requires_automation_scope(monkeypatch, tmp_path, sample_eml):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")
    client = TestClient(app)

    resp = client.post(
        f"/automation/messages/{mid}/recommendation",
        headers={"Authorization": "Bearer read-token"},
        json={
            "message_id": mid,
            "classification": "political",
            "confidence": 0.95,
            "actions": [{"type": "archive"}],
        },
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing scope: automation:recommend"


def test_request_classification_posts_to_hermes_webhook(
    monkeypatch, tmp_path, sample_eml
):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")

    import app.main as main

    seen = {}

    def fake_send(message):
        seen["message"] = message
        return 202

    monkeypatch.setattr(main.hermes_webhook, "send_classification_request", fake_send)
    client = TestClient(app)

    resp = client.post(
        f"/automation/messages/{mid}/classify",
        headers={"Authorization": "Bearer automation-token"},
    )

    assert resp.status_code == 200
    assert resp.json()["webhook_status"] == 202
    assert seen["message"]["id"] == mid
    assert seen["message"]["text"].startswith("Hello")


def test_request_classification_requires_classify_scope(monkeypatch, tmp_path, sample_eml):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")
    client = TestClient(app)

    resp = client.post(
        f"/automation/messages/{mid}/classify",
        headers={"Authorization": "Bearer read-token"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing scope: automation:classify"


def test_auto_classify_inserted_messages(monkeypatch, tmp_path, sample_eml):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")

    import app.main as main

    seen = []

    def fake_send(message):
        seen.append(message["id"])
        return 202

    monkeypatch.setattr(main.hermes_webhook, "send_classification_request", fake_send)
    main._auto_classify_inserted([mid], "test")

    assert seen == [mid]


def test_auto_classify_respects_per_sync_limit(monkeypatch, tmp_path, sample_eml):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    idx.upsert_messages("INBOX", [("2", parse_email(sample_eml), True, False)])
    mids = [stable_id("default", "INBOX", "1"), stable_id("default", "INBOX", "2")]

    import app.main as main

    main.settings.automation.auto_classify_limit_per_sync = 1
    seen = []

    def fake_send(message):
        seen.append(message["id"])
        return 202

    monkeypatch.setattr(main.hermes_webhook, "send_classification_request", fake_send)
    main._auto_classify_inserted(mids, "test")

    assert seen == [mids[0]]
