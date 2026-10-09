-- Справочник постоянных проектов «Работа»: ежемесячная плата и итог «приходит в месяц».
-- Раздел независим от денег «Сводки» и от Beads: отдельная таблица и свои агрегаты,
-- существующие таблицы/вью/агрегаты не трогаются.
PRAGMA foreign_keys = OFF;

BEGIN;

CREATE TABLE work_projects (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    client               TEXT    NULL,
    role                 TEXT    NULL,
    format               TEXT    NOT NULL DEFAULT 'other'
                         CHECK (format IN ('full_time','part_time','contract','other')),
    monthly_amount_minor INTEGER NOT NULL DEFAULT 0 CHECK (monthly_amount_minor >= 0),
    currency             TEXT    NOT NULL DEFAULT 'RUB' CHECK (length(currency) = 3),
    status               TEXT    NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','paused','finished')),
    started_on           TEXT    NULL CHECK (started_on IS NULL OR started_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    notes                TEXT    NULL,
    url                  TEXT    NULL,
    -- Задел под ручной порядок; в текущем интерфейсе не используется.
    sort_order           INTEGER NULL CHECK (sort_order IS NULL OR sort_order > 0),
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_work_projects_status ON work_projects(status);

-- Автообновление updated_at — тот же приём, что у projects.
CREATE TRIGGER trg_work_projects_updated AFTER UPDATE ON work_projects
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE work_projects SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

PRAGMA foreign_key_check;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('009_work_projects', strftime('%Y-%m-%dT%H:%M:%SZ','now'));

COMMIT;

PRAGMA foreign_keys = ON;