-- Очередь команд к Beads из интерфейса и скрытые проекты.
--
-- Борт на mini не имеет bd: команды пишутся в эту очередь, а sync-агент
-- на MacBook забирает их, выполняет через bd и отправляет свежий снимок.
-- Скрытые проекты убираются только из списка Борта, файлы на Mac не трогаются.
BEGIN;

CREATE TABLE beads_commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT    NOT NULL CHECK (length(trim(project)) > 0),
    kind         TEXT    NOT NULL CHECK (kind IN ('create','update','close','reopen','sync')),
    payload      TEXT    NOT NULL DEFAULT '{}',
    status       TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','error')),
    error        TEXT    NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    processed_at TEXT    NULL
);

CREATE INDEX idx_beads_commands_pending ON beads_commands(status, id);
CREATE INDEX idx_beads_commands_project ON beads_commands(project, id);

CREATE TABLE beads_hidden_projects (
    name       TEXT PRIMARY KEY CHECK (length(trim(name)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT INTO schema_migrations (version, applied_at)
VALUES ('007_beads_admin', strftime('%Y-%m-%dT%H:%M:%SZ','now'));

COMMIT;
