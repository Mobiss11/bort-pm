"""CRUD справочника постоянных проектов «Работа».

Раздел независим от денег «Сводки» и от Beads: своя таблица `work_projects`,
свои агрегаты. Ежемесячная плата — INTEGER копейки, как везде в проекте.
Чистые функции (conn, args) -> dict, без зависимости от FastAPI.
"""

from .. import config, errors
from ..models import WorkProjectCreate, WorkProjectUpdate, validate_payload

# Подписи для select'ов. Источник — этот список; отдельный раздел в ENUMS не заводим,
# чтобы не трогать семантику существующих справочников.
WORK_FORMATS = [
    ("full_time", "Полная занятость"),
    ("part_time", "Частичная занятость"),
    ("contract", "Контракт"),
    ("other", "Другое"),
]
WORK_STATUSES = [
    ("active", "Активен"),
    ("paused", "На паузе"),
    ("finished", "Завершён"),
]

# active → paused → finished, затем по имени без учёта регистра (lower() как в проектах).
_ORDER = """
ORDER BY
    CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
    lower(name),
    id
"""

_COLUMNS = (
    "name", "client", "role", "format", "monthly_amount_minor",
    "currency", "status", "started_on", "notes", "url",
)


def create_work_project(conn, data: dict) -> dict:
    payload = validate_payload(WorkProjectCreate, data)
    values = [getattr(payload, name) for name in _COLUMNS]
    cur = conn.execute(
        f"INSERT INTO work_projects ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' for _ in _COLUMNS)})",
        values,
    )
    conn.commit()
    return get_work_project(conn, cur.lastrowid)


def get_work_project(conn, work_id: int) -> dict:
    row = conn.execute("SELECT * FROM work_projects WHERE id = ?", (work_id,)).fetchone()
    if row is None:
        raise errors.NotFound(f"Проект работы {work_id} не найден", details={"work_id": work_id})
    return dict(row)


def list_work_projects(conn, *, status: str | None = None) -> list[dict]:
    if status is not None and status not in {code for code, _ in WORK_STATUSES}:
        raise errors.ValidationError(
            f"Неизвестный статус: {status}", details={"allowed": [code for code, _ in WORK_STATUSES]}
        )
    where = "WHERE status = ?" if status is not None else ""
    params = [status] if status is not None else []
    rows = conn.execute(f"SELECT * FROM work_projects {where} {_ORDER}", params).fetchall()
    return [dict(r) for r in rows]


def update_work_project(conn, work_id: int, data: dict) -> dict:
    current = get_work_project(conn, work_id)
    fields = validate_payload(WorkProjectUpdate, data).model_dump(exclude_unset=True)
    if not fields:
        return current
    cols = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE work_projects SET {cols} WHERE id = ?",
        [*fields.values(), work_id],
    )
    conn.commit()
    return get_work_project(conn, work_id)


def delete_work_project(conn, work_id: int) -> None:
    get_work_project(conn, work_id)
    conn.execute("DELETE FROM work_projects WHERE id = ?", (work_id,))
    conn.commit()


def work_totals(conn) -> dict:
    """Итоги справочника: «приходит в месяц» по активным в базовой валюте.

    Чужие валюты в суммы не подмешиваются (как в «Сводке»), но перечисляются
    отдельно, чтобы разница не выглядела пропажей. `paused_minor` — «потенциал»:
    сколько приходило бы, если вернуть паузы в работу.
    """
    base = config.BASE_CURRENCY
    rows = conn.execute(
        "SELECT id, name, status, currency, monthly_amount_minor FROM work_projects"
    ).fetchall()
    active_minor = sum(
        r["monthly_amount_minor"] for r in rows if r["status"] == "active" and r["currency"] == base
    )
    paused_minor = sum(
        r["monthly_amount_minor"] for r in rows if r["status"] == "paused" and r["currency"] == base
    )
    counts = {
        "active": sum(1 for r in rows if r["status"] == "active"),
        "paused": sum(1 for r in rows if r["status"] == "paused"),
        "finished": sum(1 for r in rows if r["status"] == "finished"),
        "total": len(rows),
    }
    foreign = [
        {
            "id": r["id"],
            "name": r["name"],
            "currency": r["currency"],
            "monthly_amount_minor": r["monthly_amount_minor"],
        }
        for r in rows
        if r["currency"] != base
    ]
    return {
        "active_minor": active_minor,
        "paused_minor": paused_minor,
        "counts": counts,
        "currency": base,
        "foreign": foreign,
    }