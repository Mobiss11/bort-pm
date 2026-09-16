"""CRUD проектов. Чистые функции (conn, args) -> dict, без зависимости от FastAPI."""

from .. import dates, errors
from ..models import ProjectCreate, ProjectUpdate, validate_payload

_STATUSES = {"idea", "active", "paused", "closed"}

_SORTS = {
    "priority": "priority ASC, lower(name) ASC",
    "deadline": "CASE WHEN deadline IS NULL THEN 1 ELSE 0 END ASC, deadline ASC, lower(name) ASC",
    "name": "lower(name) ASC",
    "created": "created_at DESC, id DESC",
}
_DEFAULT_SORT = "priority"


def create_project(conn, data: dict) -> dict:
    payload = validate_payload(ProjectCreate, data)
    cur = conn.execute(
        """
        INSERT INTO projects
            (name, status, priority, deal_amount_minor, currency,
             deadline, started_on, finished_on, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.status,
            payload.priority,
            payload.deal_amount_minor,
            payload.currency,
            payload.deadline,
            payload.started_on,
            payload.finished_on,
            payload.notes,
        ),
    )
    conn.commit()
    return get_project(conn, cur.lastrowid)


def get_project(conn, project_id: int) -> dict:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise errors.NotFound(f"Проект {project_id} не найден", details={"project_id": project_id})
    return dict(row)


def list_projects(
    conn,
    *,
    status: str | None = None,
    priority: int | None = None,
    q: str | None = None,
    limit: int | None = 50,
    offset: int = 0,
    sort: str | None = None,
) -> dict:
    clauses, params = [], []
    if sort is not None and sort not in _SORTS:
        raise errors.ValidationError(
            f"Неизвестная сортировка: {sort}", details={"allowed": sorted(_SORTS)}
        )
    if status is not None:
        if status not in _STATUSES:
            raise errors.ValidationError(
                f"Неизвестный статус: {status}", details={"allowed": sorted(_STATUSES)}
            )
        clauses.append("status = ?")
        params.append(status)
    if priority is not None:
        clauses.append("priority = ?")
        params.append(priority)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = _SORTS[sort] if sort is not None else _SORTS[_DEFAULT_SORT]

    rows = conn.execute(f"SELECT * FROM projects {where} ORDER BY {order}", params).fetchall()
    items = [dict(r) for r in rows]

    if q:
        # casefold в Python: lower() в SQLite не работает с кириллицей (см. §9 документа)
        needle = str(q).casefold()
        items = [p for p in items if needle in p["name"].casefold()]

    total = len(items)
    start = max(offset or 0, 0)
    page = items[start:] if limit is None else items[start : start + max(limit, 0)]
    return {"items": page, "total": total}


def _autofill_status_dates(current: dict, fields: dict) -> None:
    """Смена статуса проставляет пустую дату старта/финиша сама.

    Только если поле пустое и его не задали явно: заполненную дату не трогаем и
    при возврате из «Закрыт» не стираем — руками поставленная дата важнее догадки.
    """
    status = fields.get("status")
    if status is None or status == current["status"]:
        return
    today = dates.today_local().isoformat()
    if status == "active" and not current["started_on"] and "started_on" not in fields:
        fields["started_on"] = today
    if status == "closed" and not current["finished_on"] and "finished_on" not in fields:
        fields["finished_on"] = today


def update_project(conn, project_id: int, data: dict) -> dict:
    current = get_project(conn, project_id)
    fields = validate_payload(ProjectUpdate, data).model_dump(exclude_unset=True)
    if not fields:
        return current
    _autofill_status_dates(current, fields)

    cols = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE projects SET {cols} WHERE id = ?",
        [*fields.values(), project_id],
    )
    conn.commit()
    return get_project(conn, project_id)


def delete_project(conn, project_id: int) -> None:
    get_project(conn, project_id)
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
