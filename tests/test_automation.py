import importlib
import sys

from fastapi.testclient import TestClient

from app.automation import validate_recommendation
from app.config import SafetyConfig
from app.index import MessageIndex, stable_id
from app.mail_parse import parse_email


def test_validate_recommendation_accepts_allowed_actions():
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


def test_validate_recommendation_treats_classification_as_metadata():
    result = validate_recommendation(
        message_id="m1",
        recommendation_message_id="m1",
        classification="new label hermes invents later",
        confidence=0.9,
        actions=[],
        safety=SafetyConfig(),
    )

    assert result.accepted is True
    assert result.rejected_reasons == []


def test_validate_recommendation_rejects_empty_or_huge_classification():
    empty = validate_recommendation(
        message_id="m1",
        recommendation_message_id="m1",
        classification=" ",
        confidence=0.9,
        actions=[],
        safety=SafetyConfig(),
    )
    huge = validate_recommendation(
        message_id="m1",
        recommendation_message_id="m1",
        classification="x" * 101,
        confidence=0.9,
        actions=[],
        safety=SafetyConfig(),
    )

    assert "classification is required" in empty.rejected_reasons
    assert "classification is too long" in huge.rejected_reasons


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
hermes:
  webhook_url: http://hermes.example/webhooks/mail-classify
  webhook_secret_env: BOTT_MAIL_HERMES_WEBHOOK_SECRET
storage:
  sqlite_path: {tmp_path / "mail.sqlite"}
  audit_log: {tmp_path / "audit.log"}
safety:
  allow_archive: true
  allow_label: true
automation:
  auto_classify_new_mail: true
  auto_classify_batch_size: 10
  callback_base_url: http://bott-mail:8080
  execute_recommendations: false
  autonomous_actions:
    - add_tag
    - archive
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


def test_unbound_recommendation_endpoint_is_not_kept(monkeypatch, tmp_path, sample_eml):
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
            "classification": "political",
            "confidence": 0.95,
            "actions": [{"type": "archive"}],
        },
    )

    assert resp.status_code == 404


def test_auto_classify_inserted_messages(monkeypatch, tmp_path, sample_eml):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")

    import app.main as main

    seen = []

    def fake_send(*, batch, messages, callback_base_url):
        seen.append([message["id"] for message in messages])
        return 202

    monkeypatch.setattr(main.hermes_webhook, "send_classification_batch", fake_send)
    main._auto_classify_inserted([mid], "test")

    assert seen == [[mid]]


def test_auto_classify_batches_all_inserted_messages(monkeypatch, tmp_path, sample_eml):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    idx.upsert_messages("INBOX", [("2", parse_email(sample_eml), True, False)])
    mids = [stable_id("default", "INBOX", "1"), stable_id("default", "INBOX", "2")]

    import app.main as main

    main.settings.automation.auto_classify_batch_size = 1
    seen = []

    def fake_send(*, batch, messages, callback_base_url):
        seen.append([message["id"] for message in messages])
        return 202

    monkeypatch.setattr(main.hermes_webhook, "send_classification_batch", fake_send)
    main._auto_classify_inserted(mids, "test")

    assert seen == [[mids[0]], [mids[1]]]


def test_batch_recommendation_callback_validates_request_binding(
    monkeypatch, tmp_path, sample_eml
):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")

    import app.main as main

    client = TestClient(app)
    row = idx.get_message(mid)
    batch = main.automation_store.create_classification_batch(
        trigger="test",
        messages=[row],
    )
    batch_id = batch["batch_id"]
    pending = main.automation_store.get_pending_batch_items(batch_id)
    request_id = next(iter(pending))

    bad_resp = client.post(
        f"/automation/classification-batches/{batch_id}/recommendations",
        headers={"Authorization": "Bearer automation-token"},
        json={
            "recommendations": [
                {
                    "request_id": "req_unknown",
                    "message_id": mid,
                    "classification": "political",
                    "confidence": 0.95,
                    "actions": [{"type": "archive"}],
                }
            ]
        },
    )
    assert bad_resp.status_code == 400

    resp = client.post(
        f"/automation/classification-batches/{batch_id}/recommendations",
        headers={"Authorization": "Bearer automation-token"},
        json={
            "recommendations": [
                {
                    "request_id": request_id,
                    "message_id": mid,
                    "classification": "political",
                    "confidence": 0.95,
                    "actions": [{"type": "archive"}],
                    "reason": "Campaign email",
                }
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted_count"] == 1
    assert body["rejected_count"] == 0
    assert body["decisions"][0]["message_id"] == mid


def test_batch_recommendation_executes_enabled_tag_then_archive(
    monkeypatch, tmp_path, sample_eml
):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")

    import app.main as main

    main.settings.automation.execute_recommendations = True
    calls = []

    def fake_add_tag(folder, uid, tag):
        calls.append(("add_tag", folder, uid, tag))

    def fake_archive(folder, uid, archive_folder):
        calls.append(("archive", folder, uid, archive_folder))

    monkeypatch.setattr(main.imap, "add_tag_message", fake_add_tag)
    monkeypatch.setattr(main.imap, "archive_message", fake_archive)

    row = idx.get_message(mid)
    batch = main.automation_store.create_classification_batch(
        trigger="test",
        messages=[row],
    )
    request_id = batch["items"][0]["request_id"]
    client = TestClient(app)

    resp = client.post(
        f"/automation/classification-batches/{batch['batch_id']}/recommendations",
        headers={"Authorization": "Bearer automation-token"},
        json={
            "recommendations": [
                {
                    "request_id": request_id,
                    "message_id": mid,
                    "classification": "political",
                    "confidence": 0.95,
                    "actions": [
                        {"type": "add_tag", "tag": "Political"},
                        {"type": "archive"},
                    ],
                    "reason": "Campaign email",
                }
            ]
        },
    )

    assert resp.status_code == 200
    assert resp.json()["accepted_count"] == 1
    assert calls == [
        ("add_tag", "INBOX", "1", "Political"),
        ("archive", "INBOX", "1", "Archive"),
    ]
    assert idx.get_message(mid) is None


def test_batch_recommendation_does_not_execute_when_disabled(
    monkeypatch, tmp_path, sample_eml
):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")

    import app.main as main

    calls = []
    monkeypatch.setattr(
        main.imap,
        "archive_message",
        lambda folder, uid, archive_folder: calls.append("archive"),
    )
    row = idx.get_message(mid)
    batch = main.automation_store.create_classification_batch(
        trigger="test",
        messages=[row],
    )
    request_id = batch["items"][0]["request_id"]
    client = TestClient(app)

    resp = client.post(
        f"/automation/classification-batches/{batch['batch_id']}/recommendations",
        headers={"Authorization": "Bearer automation-token"},
        json={
            "recommendations": [
                {
                    "request_id": request_id,
                    "message_id": mid,
                    "classification": "political",
                    "confidence": 0.95,
                    "actions": [{"type": "archive"}],
                }
            ]
        },
    )

    assert resp.status_code == 200
    assert calls == []
    assert idx.get_message(mid) is not None


def test_batch_recommendation_stops_actions_after_failure(
    monkeypatch, tmp_path, sample_eml
):
    app, db_path = _load_app(monkeypatch, tmp_path)
    idx = MessageIndex(db_path, account="default")
    idx.upsert_messages("INBOX", [("1", parse_email(sample_eml), True, False)])
    mid = stable_id("default", "INBOX", "1")

    import app.main as main

    main.settings.automation.execute_recommendations = True
    calls = []

    def fake_add_tag(folder, uid, tag):
        calls.append(("add_tag", folder, uid, tag))
        raise RuntimeError("label missing")

    def fake_archive(folder, uid, archive_folder):
        calls.append(("archive", folder, uid, archive_folder))

    monkeypatch.setattr(main.imap, "add_tag_message", fake_add_tag)
    monkeypatch.setattr(main.imap, "archive_message", fake_archive)

    row = idx.get_message(mid)
    batch = main.automation_store.create_classification_batch(
        trigger="test",
        messages=[row],
    )
    request_id = batch["items"][0]["request_id"]
    client = TestClient(app)

    resp = client.post(
        f"/automation/classification-batches/{batch['batch_id']}/recommendations",
        headers={"Authorization": "Bearer automation-token"},
        json={
            "recommendations": [
                {
                    "request_id": request_id,
                    "message_id": mid,
                    "classification": "political",
                    "confidence": 0.95,
                    "actions": [
                        {"type": "add_tag", "tag": "Political"},
                        {"type": "archive"},
                    ],
                }
            ]
        },
    )

    assert resp.status_code == 200
    assert calls == [("add_tag", "INBOX", "1", "Political")]
    assert idx.get_message(mid) is not None
