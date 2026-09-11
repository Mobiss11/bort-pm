-- 003: платежи по проекту (стадии оплаты: предоплата / частичная / финальная)
BEGIN;

CREATE TABLE payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    paid_on      TEXT    NOT NULL CHECK (paid_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    kind         TEXT    NOT NULL DEFAULT 'partial'
                 CHECK (kind IN ('prepayment','partial','final')),
    comment      TEXT    NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_payments_project ON payments(project_id);
CREATE INDEX idx_payments_paid_on ON payments(paid_on);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('003_payments', strftime('%Y-%m-%dT%H:%M:%SZ','now'));

COMMIT;
