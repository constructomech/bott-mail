-- bott-mail-service initial schema (Phase 1, read-only)

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  folder TEXT NOT NULL,
  uid TEXT NOT NULL,
  message_id TEXT,
  thread_key TEXT,
  subject TEXT,
  from_addr TEXT,
  to_addrs TEXT,
  cc_addrs TEXT,
  date_utc TEXT,
  unread INTEGER NOT NULL DEFAULT 0,
  flagged INTEGER NOT NULL DEFAULT 0,
  has_attachments INTEGER NOT NULL DEFAULT 0,
  snippet TEXT,
  body_text TEXT,
  body_sha256 TEXT,
  raw_headers TEXT,
  attachments_json TEXT,
  synced_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_folder_uid
  ON messages(account, folder, uid);

CREATE INDEX IF NOT EXISTS idx_messages_date
  ON messages(date_utc);

CREATE INDEX IF NOT EXISTS idx_messages_from
  ON messages(from_addr);

-- External-content FTS5 table. Rows are kept in sync manually in index.py
-- (insert on new message, update on change) so we avoid trigger complexity.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  subject,
  from_addr,
  body_text,
  content='messages',
  content_rowid='rowid'
);
