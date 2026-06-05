from datetime import datetime, timezone

from app.index import MessageIndex, stable_id
from app.mail_parse import parse_email


def _index_sample(tmp_db, raw, uid="1", folder="INBOX", unread=True):
    idx = MessageIndex(tmp_db, account="test")
    pm = parse_email(raw)
    idx.upsert_messages(folder, [(uid, pm, unread, False)])
    return idx


def test_search_finds_term(tmp_db, sample_eml):
    idx = _index_sample(tmp_db, sample_eml)
    results = idx.search("insurance renewal", days=365, limit=10)
    assert len(results) == 1
    assert results[0]["subject"] == "Your insurance renewal notice"
    assert results[0]["from"] == "notices@insurance.example.com"


def test_search_no_match(tmp_db, sample_eml):
    idx = _index_sample(tmp_db, sample_eml)
    assert idx.search("zzzznomatch", days=365, limit=10) == []


def test_sync_idempotent(tmp_db, sample_eml):
    idx = MessageIndex(tmp_db, account="test")
    pm = parse_email(sample_eml)
    r1 = idx.upsert_messages("INBOX", [("1", pm, True, False)])
    r2 = idx.upsert_messages("INBOX", [("1", pm, True, False)])
    assert r1.inserted == 1 and r1.updated == 0
    assert r2.inserted == 0 and r2.updated == 1
    # No duplicate rows
    results = idx.search("insurance", days=365, limit=10)
    assert len(results) == 1


def test_recent_unread_filter(tmp_db, sample_eml):
    idx = MessageIndex(tmp_db, account="test")
    pm = parse_email(sample_eml)
    idx.upsert_messages("INBOX", [("1", pm, True, False)])
    idx.upsert_messages("INBOX", [("2", pm, False, False)])
    all_recent = idx.recent(days=365, limit=10, unread_only=False)
    unread = idx.recent(days=365, limit=10, unread_only=True)
    assert len(all_recent) == 2
    assert len(unread) == 1


def test_get_message_detail(tmp_db, sample_eml):
    idx = _index_sample(tmp_db, sample_eml)
    mid = stable_id("test", "INBOX", "1")
    detail = idx.get_message(mid)
    assert detail is not None
    assert detail["text"].startswith("Hello")
    assert detail["cc"] == []


def test_stable_id_deterministic():
    a = stable_id("acct", "INBOX", "5")
    b = stable_id("acct", "INBOX", "5")
    assert a == b and len(a) == 64


def test_folder_state_roundtrip(tmp_db):
    idx = MessageIndex(tmp_db, account="test")
    assert idx.get_folder_state("INBOX") == (None, 0)
    idx.set_folder_state("INBOX", uidvalidity=12345, last_uid=42)
    assert idx.get_folder_state("INBOX") == (12345, 42)
    idx.set_folder_state("INBOX", uidvalidity=12345, last_uid=99)
    assert idx.get_folder_state("INBOX") == (12345, 99)


def test_update_flags(tmp_db, sample_eml):
    idx = MessageIndex(tmp_db, account="test")
    pm = parse_email(sample_eml)
    idx.upsert_messages("INBOX", [("7", pm, True, False)])
    changed = idx.update_flags("INBOX", {7: (False, True)})
    assert changed == 1
    mid = stable_id("test", "INBOX", "7")
    assert idx.get_message(mid)["unread"] is False
    assert idx.update_flags("INBOX", {7: (False, True)}) == 0


def test_uid_coerced_to_str(tmp_db, sample_eml):
    idx = MessageIndex(tmp_db, account="test")
    pm = parse_email(sample_eml)
    idx.upsert_messages("INBOX", [(7, pm, True, False)])  # int uid
    mid = stable_id("test", "INBOX", "7")  # str uid
    assert idx.get_message(mid) is not None
