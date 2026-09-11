"""CRUD задач. Задачи опциональны: проект живёт и без них."""

from .. import errors
from ..models import TaskCreate, TaskUpdate, validate_payload
from . import projects

_OPEN_STATUSES = {"todo", "in_progress", "review"}


def create_task(conn, project_id: int, data: dict) -> dict:
    projects.get_project(conn, project_id)
    payload = validate_payload(TaskCreate, data)
    cur = conn.execute(
        """
        INSERT INTO tasks (project_id, title, status, priority, deadline, notes, position)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            payload.title,
            payload.status,
            payload.priority,
            payload.deadline,
            payload.notes,
            payload.position,
        ),
    )
    conn.commit()
    return get_task(conn, cur.lastrowid)


def get_task(conn, task_id: int) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise errors.NotFound(f"Задача {task_id} не найдена", details={"task_id": task_id})
    return dict(row)


def list_tasks(conn, project_id: int, *, status: str | None = None) -> list[dict]:
    projects.get_project(conn, project_id)
    if status is not None:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND status = ? "
            "ORDER BY position ASC, priority ASC, id ASC",
            (project_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? "
            "ORDER BY position ASC, priority ASC, id ASC",
            (project_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_tasks(
    conn,
    *,
    status: str | None = None,
    project_id: int | None = None,
    q: str | None = None,
) -> list[dict]:
    """Задачи всех проектов (глобальный канбан) с названием проекта.

    Поиск q — casefold в Python: lower() в SQLite не работает с кириллицей (§9).
    """
    clauses, params = [], []
    if status is not None:
        clauses.append("t.status = ?")
        params.append(status)
    if project_id is not None:
        clauses.append("t.project_id = ?")
        params.append(project_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT t.*, p.name AS project_name
        FROM tasks t
        JOIN projects p ON p.id = t.project_id
        {where}
        ORDER BY t.priority ASC, t.deadline IS NULL, t.deadline ASC, t.id ASC
        """,
        params,
    ).fetchall()
    items = [dict(r) for r in rows]
    if q:
        needle = str(q).casefold()
        items = [t for t in items if needle in t["title"].casefold()]
    return items


def update_task(conn, task_id: int, data: dict) -> dict:
    current = get_task(conn, task_id)
    fields = validate_payload(TaskUpdate, data).model_dump(exclude_unset=True)
    if not fields:
        return current

    cols = [f"{name} = ?" for name in fields]
    params = list(fields.values())

    new_status = fields.get("status")
    if new_status in ("done", "cancelled") and current["closed_at"] is None:
        cols.append("closed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    elif new_status is not None and new_status in _OPEN_STATUSES and current["closed_at"] is not None:
        cols.append("closed_at = NULL")

    conn.execute(
        f"UPDATE tasks SET {', '.join(cols)} WHERE id = ?",
        [*params, task_id],
    )
    conn.commit()
    return get_task(conn, task_id)


def close_task(conn, task_id: int, outcome: str = "done") -> dict:
    if outcome not in ("done", "cancelled"):
        raise errors.ValidationError(
            "outcome должен быть 'done' или 'cancelled'", details={"got": outcome}
        )
    get_task(conn, task_id)
    conn.execute(
        "UPDATE tasks SET status = ?, closed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE id = ?",
        (outcome, task_id),
    )
    conn.commit()
    return get_task(conn, task_id)


def delete_task(conn, task_id: int) -> None:
    get_task(conn, task_id)
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
