-- bott-mail-service: per-folder incremental sync state.

CREATE TABLE IF NOT EXISTS folder_state (
  account TEXT NOT NULL,
  folder TEXT NOT NULL,
  uidvalidity INTEGER,
  last_uid INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT,
  PRIMARY KEY (account, folder)
);
