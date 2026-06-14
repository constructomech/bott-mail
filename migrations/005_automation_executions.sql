-- Execution audit records for request-bound autonomous recommendations.

CREATE TABLE IF NOT EXISTS automation_executions (
  id TEXT PRIMARY KEY,
  account TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(decision_id) REFERENCES automation_decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_automation_executions_decision
  ON automation_executions(decision_id);

CREATE INDEX IF NOT EXISTS idx_automation_executions_batch
  ON automation_executions(batch_id, request_id);
