-- 004: журнал AI-запросов и снимки лимитов провайдеров
BEGIN;

CREATE TABLE ai_usage_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT    NOT NULL CHECK (length(trim(provider)) > 0),
    agent               TEXT    NOT NULL DEFAULT 'unknown' CHECK (length(trim(agent)) > 0),
    model               TEXT    NOT NULL CHECK (length(trim(model)) > 0),
    project_id          INTEGER NULL REFERENCES projects(id) ON DELETE SET NULL,
    external_request_id TEXT    NULL,
    input_tokens        INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
    output_tokens       INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0 CHECK (cache_read_tokens >= 0),
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (cache_write_tokens >= 0),
    cost_microusd       INTEGER NOT NULL DEFAULT 0 CHECK (cost_microusd >= 0),
    duration_ms         INTEGER NULL CHECK (duration_ms IS NULL OR duration_ms >= 0),
    status              TEXT    NOT NULL DEFAULT 'success'
                        CHECK (status IN ('success', 'error')),
    error_code          TEXT    NULL,
    occurred_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE (provider, external_request_id)
);

CREATE INDEX idx_ai_usage_occurred ON ai_usage_events(occurred_at DESC);
CREATE INDEX idx_ai_usage_provider_occurred ON ai_usage_events(provider, occurred_at DESC);
CREATE INDEX idx_ai_usage_project ON ai_usage_events(project_id) WHERE project_id IS NOT NULL;

CREATE TABLE ai_limit_snapshots (
    provider     TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    source       TEXT NOT NULL,
    payload_json TEXT NULL,
    captured_at  TEXT NULL,
    checked_at   TEXT NOT NULL,
    message      TEXT NULL
);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('004_ai_usage', strftime('%Y-%m-%dT%H:%M:%SZ','now'));

COMMIT;
