-- Request-bound classification batches for autonomous new-mail recommendations.

CREATE TABLE IF NOT EXISTS classification_batches (
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  status TEXT NOT NULL,
  trigger TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS classification_batch_items (
  request_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  account TEXT NOT NULL,
  message_id TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(batch_id) REFERENCES classification_batches(id)
);

CREATE INDEX IF NOT EXISTS idx_classification_batch_items_batch
  ON classification_batch_items(batch_id, status);

CREATE INDEX IF NOT EXISTS idx_classification_batch_items_message
  ON classification_batch_items(account, message_id);
