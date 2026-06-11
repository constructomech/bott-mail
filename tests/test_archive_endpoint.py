import importlib
import sys

from fastapi.testclient import TestClient


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
    - id: archive-worker
      token_env: BOTT_MAIL_WRITE_TOKEN
      scopes:
        - messages:archive
storage:
  sqlite_path: {tmp_path / "mail.sqlite"}
  audit_log: {tmp_path / "audit.log"}
safety:
  allow_archive: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOTT_MAIL_CONFIG", str(config))
    monkeypatch.setenv("BOTT_MAIL_READ_TOKEN", "read-token")
    monkeypatch.setenv("BOTT_MAIL_ARCHIVE_TOKEN", "archive-token")

    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main").app


def test_read_token_cannot_call_archive_endpoint(monkeypatch, tmp_path):
    app = _load_app(monkeypatch, tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/messages/not-real/archive",
        headers={"Authorization": "Bearer read-token"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing scope: messages:archive"


def test_legacy_read_token_cannot_call_archive_endpoint(monkeypatch, tmp_path):
    config = tmp_path / "legacy-config.yaml"
    config.write_text(
        f"""
auth:
  read_token_env: BOTT_MAIL_READ_TOKEN
storage:
  sqlite_path: {tmp_path / "legacy-mail.sqlite"}
  audit_log: {tmp_path / "legacy-audit.log"}
safety:
  allow_archive: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOTT_MAIL_CONFIG", str(config))
    monkeypatch.setenv("BOTT_MAIL_READ_TOKEN", "read-token")

    sys.modules.pop("app.main", None)
    app = importlib.import_module("app.main").app
    client = TestClient(app)

    resp = client.post(
        "/messages/not-real/archive",
        headers={"Authorization": "Bearer read-token"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Missing scope: messages:archive"
