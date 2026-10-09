-- Новый статус проекта «Ожидаем оплату»: работа сдана, деньги ещё не пришли.
-- SQLite не умеет менять CHECK: пересобираем таблицу по официальной процедуре.
-- foreign_keys выключаем до BEGIN, иначе DROP TABLE каскадно удалит задачи и оплаты.
PRAGMA foreign_keys = OFF;

BEGIN;

DROP VIEW IF EXISTS v_project_summary;

CREATE TABLE projects_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    status            TEXT    NOT NULL DEFAULT 'idea'
                      CHECK (status IN ('idea','discussion','active','review','payment','paused','closed')),
    priority          INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 4),
    deal_amount_minor INTEGER NOT NULL DEFAULT 0 CHECK (deal_amount_minor >= 0),
    currency          TEXT    NOT NULL DEFAULT 'RUB' CHECK (length(currency) = 3),
    deadline          TEXT    NULL CHECK (deadline IS NULL OR deadline GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    started_on        TEXT    NULL CHECK (started_on IS NULL OR started_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    finished_on       TEXT    NULL CHECK (finished_on IS NULL OR finished_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    notes             TEXT    NULL,
    -- Ручной порядок в сводке. NULL — проект в автосортировке (горящее,
    -- приоритет, дедлайн, имя); число — место, поставленное перетаскиванием.
    sort_order        INTEGER NULL CHECK (sort_order IS NULL OR sort_order > 0),
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT INTO projects_new
    (id, name, status, priority, deal_amount_minor, currency,
     deadline, started_on, finished_on, notes, sort_order, created_at, updated_at)
SELECT id, name, status, priority, deal_amount_minor, currency,
       deadline, started_on, finished_on, notes, sort_order, created_at, updated_at
FROM projects;

DROP TABLE projects;
ALTER TABLE projects_new RENAME TO projects;

CREATE INDEX idx_projects_status_priority ON projects(status, priority);
CREATE INDEX idx_projects_deadline        ON projects(deadline) WHERE deadline IS NOT NULL;

-- Возвращаем триггер автообновления updated_at, снятый вместе с таблицей.
CREATE TRIGGER trg_projects_updated AFTER UPDATE ON projects
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE projects SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE VIEW v_project_summary AS
SELECT
    p.id, p.name, p.status, p.priority, p.currency,
    p.deal_amount_minor,
    COALESCE(e.expenses_minor, 0)                          AS expenses_minor,
    p.deal_amount_minor - COALESCE(e.expenses_minor, 0)    AS margin_minor,
    COALESCE(e.expenses_count, 0)                          AS expenses_count,
    p.deadline, p.started_on, p.finished_on,
    COALESCE(t.tasks_total, 0)                             AS tasks_total,
    COALESCE(t.tasks_done, 0)                              AS tasks_done,
    COALESCE(t.tasks_open, 0)                              AS tasks_open,
    t.next_task_deadline,
    p.sort_order,
    p.created_at, p.updated_at
FROM projects p
LEFT JOIN v_project_expense_stats e ON e.project_id = p.id
LEFT JOIN v_project_task_stats    t ON t.project_id = p.id;

PRAGMA foreign_key_check;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('008_project_payment_status', strftime('%Y-%m-%dT%H:%M:%SZ','now'));

COMMIT;

PRAGMA foreign_keys = ON;
