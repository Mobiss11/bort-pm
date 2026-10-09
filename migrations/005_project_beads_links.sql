-- Явная связь проекта Борта с одним доступным локальным проектом задач Beads.
-- Сами задачи остаются в Beads; удаление проекта Борта убирает только связь.
BEGIN;
CREATE TABLE project_beads_links (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    beads_project TEXT NOT NULL UNIQUE CHECK (length(trim(beads_project)) > 0),
    linked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
INSERT INTO schema_migrations (version, applied_at)
VALUES ('005_project_beads_links', strftime('%Y-%m-%dT%H:%M:%SZ','now'));
COMMIT;
