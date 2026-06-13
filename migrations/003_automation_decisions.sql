-- Dry-run automation recommendation records.

CREATE TABLE IF NOT EXISTS automation_decisions (
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  message_id TEXT NOT NULL,
  rule_name TEXT,
  classification TEXT NOT NULL,
  confidence REAL NOT NULL,
  accepted INTEGER NOT NULL,
  rejected_reasons TEXT NOT NULL,
  actions_json TEXT NOT NULL,
  raw_recommendation_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_automation_decisions_message
  ON automation_decisions(account, message_id, created_at);
