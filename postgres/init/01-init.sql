CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS task_ledger (
    id BIGSERIAL PRIMARY KEY,
    issue_id TEXT NOT NULL,
    node TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    pr_url TEXT,
    error TEXT,
    total_cost_usd NUMERIC(6,4) DEFAULT 0,
    healing_attempts INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_task_ledger_status ON task_ledger(status);
CREATE INDEX IF NOT EXISTS idx_task_ledger_issue ON task_ledger(issue_id);