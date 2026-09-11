PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE projects (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    status            TEXT    NOT NULL DEFAULT 'idea'
                      CHECK (status IN ('idea','active','paused','closed')),
    priority          INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 4),
    deal_amount_minor INTEGER NOT NULL DEFAULT 0 CHECK (deal_amount_minor >= 0),
    currency          TEXT    NOT NULL DEFAULT 'RUB' CHECK (length(currency) = 3),
    deadline          TEXT    NULL CHECK (deadline IS NULL OR deadline GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    started_on        TEXT    NULL CHECK (started_on IS NULL OR started_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    finished_on       TEXT    NULL CHECK (finished_on IS NULL OR finished_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    notes             TEXT    NULL,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_projects_status_priority ON projects(status, priority);
CREATE INDEX idx_projects_deadline        ON projects(deadline) WHERE deadline IS NOT NULL;

CREATE TABLE tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL CHECK (length(trim(title)) > 0),
    status     TEXT    NOT NULL DEFAULT 'todo'
               CHECK (status IN ('todo','in_progress','review','done','cancelled')),
    priority   INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 4),
    deadline   TEXT    NULL CHECK (deadline IS NULL OR deadline GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    notes      TEXT    NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    closed_at  TEXT    NULL
);

CREATE INDEX idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX idx_tasks_deadline       ON tasks(deadline) WHERE deadline IS NOT NULL;

CREATE TABLE expense_categories (
    code       TEXT    PRIMARY KEY,
    title_ru   TEXT    NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 100,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

CREATE TABLE expenses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    amount_minor  INTEGER NOT NULL CHECK (amount_minor > 0),
    currency      TEXT    NOT NULL DEFAULT 'RUB' CHECK (length(currency) = 3),
    spent_on      TEXT    NOT NULL CHECK (spent_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    category_code TEXT    NOT NULL REFERENCES expense_categories(code),
    comment       TEXT    NULL,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX idx_expenses_project  ON expenses(project_id);
CREATE INDEX idx_expenses_spent_on ON expenses(spent_on);
CREATE INDEX idx_expenses_category ON expenses(category_code);

CREATE TABLE chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL DEFAULT 'group' CHECK (kind IN ('private','group')),
    title      TEXT    NOT NULL CHECK (length(trim(title)) > 0),
    tg_chat_id TEXT    NULL UNIQUE,
    tg_link    TEXT    NULL,
    notes      TEXT    NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE people (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name    TEXT    NOT NULL CHECK (length(trim(full_name)) > 0),
    tg_username  TEXT    NULL UNIQUE,
    notes        TEXT    NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE project_chats (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chat_id    INTEGER NOT NULL REFERENCES chats(id)    ON DELETE CASCADE,
    note       TEXT    NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (project_id, chat_id)
);
CREATE INDEX idx_project_chats_chat ON project_chats(chat_id);

CREATE TABLE chat_members (
    chat_id    INTEGER NOT NULL REFERENCES chats(id)  ON DELETE CASCADE,
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role       TEXT    NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (chat_id, person_id)
);
CREATE INDEX idx_chat_members_person ON chat_members(person_id);

CREATE TABLE project_people (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    person_id  INTEGER NOT NULL REFERENCES people(id)   ON DELETE CASCADE,
    role       TEXT    NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (project_id, person_id)
);
CREATE INDEX idx_project_people_person ON project_people(person_id);

-- Автообновление updated_at

CREATE TRIGGER trg_projects_updated AFTER UPDATE ON projects
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE projects SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_tasks_updated AFTER UPDATE ON tasks
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE tasks SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_expenses_updated AFTER UPDATE ON expenses
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE expenses SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_chats_updated AFTER UPDATE ON chats
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE chats SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_people_updated AFTER UPDATE ON people
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE people SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

-- Представления

CREATE VIEW v_project_task_stats AS
SELECT
    project_id,
    COUNT(*)                                                          AS tasks_total,
    SUM(CASE WHEN status = 'done'      THEN 1 ELSE 0 END)             AS tasks_done,
    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END)             AS tasks_cancelled,
    SUM(CASE WHEN status NOT IN ('done','cancelled') THEN 1 ELSE 0 END) AS tasks_open,
    MIN(CASE WHEN status NOT IN ('done','cancelled') THEN deadline END) AS next_task_deadline
FROM tasks
GROUP BY project_id;

CREATE VIEW v_project_expense_stats AS
SELECT
    project_id,
    COALESCE(SUM(amount_minor), 0) AS expenses_minor,
    COUNT(*)                       AS expenses_count
FROM expenses
GROUP BY project_id;

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
    p.created_at, p.updated_at
FROM projects p
LEFT JOIN v_project_expense_stats e ON e.project_id = p.id
LEFT JOIN v_project_task_stats    t ON t.project_id = p.id;

INSERT INTO schema_migrations (version, applied_at)
VALUES ('001_init', strftime('%Y-%m-%dT%H:%M:%SZ','now'));

COMMIT;
