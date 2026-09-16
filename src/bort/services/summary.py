"""Сводка поверх v_project_summary: сортировка §3.4, deadline_state, агрегаты.

«Сегодня» приходит параметром из dates.today_local() (BORT_TZ) — julianday('now')
в SQL не используется, чтобы вечером не сдвигать дедлайны на день (см. §9).
"""

from .. import config, dates, errors
from . import projects

_SCOPES = {
    "open": "status != 'closed'",
    "active": "status = 'active'",
    "closed": "status = 'closed'",
    "all": "1 = 1",
}

# «Горящее сверху, потом по приоритету» — раздел 3.4 документа
_SUMMARY_ORDER = """
ORDER BY
    CASE WHEN deadline IS NOT NULL
          AND julianday(deadline) - julianday(:today) <= 3 THEN 0 ELSE 1 END,
    priority ASC,
    CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
    deadline ASC,
    lower(name)
"""


def _resolve_today(today):
    if today is None:
        return dates.today_local()
    try:
        return dates.parse_date(today)
    except ValueError as e:
        raise errors.ValidationError(str(e), details={"today": today}) from e


_TASKS_FILTERS = ("any", "open", "closed")

_OPEN_TASK_STATUSES = ("todo", "in_progress", "review")

# Фактическая следующая задача проекта: срочнее выше, начатые раньше todo,
# без дедлайна — в конце, среди равных — стабильный порядок по id.
_NEXT_TASK_ORDER = """
ORDER BY t.project_id,
    t.priority ASC,
    CASE WHEN t.status = 'todo' THEN 1 ELSE 0 END ASC,
    t.deadline IS NULL ASC,
    t.deadline ASC,
    t.id ASC
"""


def _next_tasks_map(conn, project_ids: list[int]) -> dict[int, dict]:
    """project_id → следующая незакрытая задача (только открытые проекты)."""
    if not project_ids:
        return {}
    qs = ",".join("?" for _ in project_ids)
    rows = conn.execute(
        f"""
        SELECT t.* FROM tasks t
        JOIN projects p ON p.id = t.project_id
        WHERE t.project_id IN ({qs})
          AND p.status != 'closed'
          AND t.status IN ('todo', 'in_progress', 'review')
        {_NEXT_TASK_ORDER}
        """,
        list(project_ids),
    ).fetchall()
    nxt: dict[int, dict] = {}
    for row in rows:
        pid = row["project_id"]
        if pid not in nxt:
            nxt[pid] = dict(row)
    return nxt


def get_attention(conn, *, today=None) -> dict:
    """Первый экран сводки: что требует внимания. Только открытые проекты.

    - overdue: открытые проекты с просроченным дедлайном (closed overdue сюда
      не попадает — по ним нельзя ничего сделать);
    - items: overdue-задачи (все незакрытые просроченные задачи открытых
      проектов — даже если следующей задачей проекта выбрана другая), затем
      следующая незакрытая задача каждого открытого проекта в бакетах
      urgent (приоритет 1) / started (в работе, на проверке) / other;
    - no_tasks: открытые проекты без незакрытых задач. Сводка их больше не
      печатает (перечень дублировал таблицу), поле осталось для потребителей
      сервиса — считается из уже загруженных строк, лишних запросов нет.
    Недатированные задачи не исчезают: они в своих бакетах последними.
    """
    today = _resolve_today(today)
    open_items = get_summary(conn, scope="open", today=today)["projects"]
    open_ids = [p["id"] for p in open_items]
    overdue = [p for p in open_items if p["deadline_state"] == "overdue"]
    no_tasks = [p for p in open_items if p["next_task"] is None]

    items: list[dict] = []
    overdue_task_ids: set[int] = set()

    # Все незакрытые просроченные задачи открытых проектов — не теряются,
    # даже если «следующей» выбрана задача с более высоким приоритетом.
    if open_ids:
        qs = ",".join("?" for _ in open_ids)
        rows = conn.execute(
            f"""
            SELECT t.*, p.name AS project_name
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            WHERE t.project_id IN ({qs})
              AND p.status != 'closed'
              AND t.status IN ('todo', 'in_progress', 'review')
              AND t.deadline IS NOT NULL
            ORDER BY t.deadline ASC, t.priority ASC, t.id ASC
            """,
            list(open_ids),
        ).fetchall()
        for row in rows:
            t = dict(row)
            if dates.deadline_state(t["deadline"], today) != "overdue":
                continue
            overdue_task_ids.add(t["id"])
            items.append(
                {
                    "project_id": t["project_id"],
                    "project_name": t["project_name"],
                    "task": t,
                    "bucket": "overdue",
                    "task_deadline_state": "overdue",
                }
            )

    for p in open_items:
        t = p["next_task"]
        if t is None or t["id"] in overdue_task_ids:
            continue
        if t["priority"] == 1:
            bucket = "urgent"
        elif t["status"] in ("in_progress", "review"):
            bucket = "started"
        else:
            bucket = "other"
        items.append(
            {
                "project_id": p["id"],
                "project_name": p["name"],
                "task": t,
                "bucket": bucket,
                "task_deadline_state": dates.deadline_state(t["deadline"], today),
            }
        )
    bucket_order = {"overdue": 0, "urgent": 1, "started": 2, "other": 3}
    items.sort(
        key=lambda i: (
            bucket_order[i["bucket"]],
            i["task"]["priority"],
            i["task"]["deadline"] is None,
            i["task"]["deadline"] or "",
            i["task"]["id"],
        )
    )
    return {"overdue": overdue, "items": items, "no_tasks": no_tasks}


def _payments_map(conn) -> dict[int, dict]:
    """project_id → сумма всех внесённых платежей, их число и дата последнего.

    Одним группированным запросом, без N+1: сводка показывает оплату по каждой
    строке, поэтому «сколько внесено» должно стоить столько же, сколько сама сводка.
    """
    rows = conn.execute(
        """
        SELECT project_id,
               SUM(amount_minor) AS paid_minor,
               COUNT(*)          AS payments_count,
               MAX(paid_on)      AS last_payment_on
        FROM payments
        GROUP BY project_id
        """
    ).fetchall()
    return {r["project_id"]: dict(r) for r in rows}


def _apply_payment_fields(p: dict, paid_map: dict[int, dict]) -> None:
    """Оплата проекта одинаково во всех потребителях: сводка, строка, карточка, MCP.

    remaining_minor может быть отрицательным — это переплата, и её надо видеть,
    а не прятать под max(0, …).
    """
    meta = paid_map.get(p["id"]) or {}
    paid = meta.get("paid_minor") or 0
    deal = p["deal_amount_minor"] or 0
    p["paid_minor"] = paid
    p["remaining_minor"] = deal - paid
    p["payment_progress"] = round(paid / deal * 100) if deal else None
    p["payments_count"] = meta.get("payments_count") or 0
    p["last_payment_on"] = meta.get("last_payment_on")


def get_summary(conn, *, scope: str = "open", q: str | None = None, today=None, tasks: str | None = None) -> dict:
    if scope not in _SCOPES:
        raise errors.ValidationError(
            f"Неизвестный scope: {scope}", details={"allowed": sorted(_SCOPES)}
        )
    if tasks is not None and tasks not in _TASKS_FILTERS:
        raise errors.ValidationError(
            f"Неизвестный фильтр задач: {tasks}", details={"allowed": list(_TASKS_FILTERS)}
        )
    today = _resolve_today(today)

    rows = conn.execute(
        f"SELECT * FROM v_project_summary WHERE {_SCOPES[scope]} {_SUMMARY_ORDER}",
        {"today": today.isoformat()},
    ).fetchall()

    paid_map = _payments_map(conn)

    items = []
    for row in rows:
        p = dict(row)
        p["deadline_state"] = dates.deadline_state(p["deadline"], today)
        _apply_payment_fields(p, paid_map)
        items.append(p)

    if q:
        needle = str(q).casefold()
        items = [p for p in items if needle in p["name"].casefold()]

    if tasks == "open":
        items = [p for p in items if p["tasks_open"] > 0]
    elif tasks == "closed":
        # Все задачи закрыты: задачи ведутся и открытых не осталось
        items = [p for p in items if p["tasks_total"] > 0 and p["tasks_open"] == 0]

    next_map = _next_tasks_map(conn, [p["id"] for p in items])
    for p in items:
        p["next_task"] = next_map.get(p["id"])

    portfolio = get_portfolio_totals(conn, today=today)
    return {
        "totals": build_totals(items),
        "projects": items,
        "portfolio_totals": portfolio,
    }


def get_portfolio_totals(conn, *, q: str | None = None, today=None) -> dict:
    """Деньги всего портфеля: открытые + закрытые (закрытые не выпадают из маржи).

    get_summary не передаёт фильтры: верхняя панель всегда по всем проектам.
    Параметр q оставлен для прямых сервисных вызовов.
    """
    if today is None:
        today = dates.today_local()
    rows = conn.execute(
        f"SELECT * FROM v_project_summary WHERE 1 = 1 {_SUMMARY_ORDER}",
        {"today": today.isoformat()},
    ).fetchall()
    items = []
    paid_map = _payments_map(conn)
    for row in rows:
        p = dict(row)
        p["deadline_state"] = dates.deadline_state(p["deadline"], today)
        _apply_payment_fields(p, paid_map)
        if q and str(q).casefold() not in p["name"].casefold():
            continue
        items.append(p)
    totals = build_totals(items)
    totals["closed_count"] = sum(1 for p in items if p["status"] == "closed")
    # Просрочка/горящие считаются только по открытым проектам: закрытый
    # просроченный дедлайн — не работа, которую можно сделать.
    totals["overdue_open_count"] = sum(
        1 for p in items if p["status"] != "closed" and p["deadline_state"] == "overdue"
    )
    totals["hot_open_count"] = sum(
        1 for p in items if p["status"] != "closed" and p["deadline_state"] == "hot"
    )
    return totals


def build_totals(items: list[dict]) -> dict:
    """Агрегаты только по базовой валюте; чужие валюты исключаются явно."""
    base = config.BASE_CURRENCY
    included = [p for p in items if p["currency"] == base]
    excluded = [
        {"id": p["id"], "name": p["name"], "currency": p["currency"]}
        for p in items
        if p["currency"] != base
    ]
    deal_total = sum(p["deal_amount_minor"] for p in included)
    paid_total = sum(p["paid_minor"] for p in included)
    return {
        "projects_count": len(included),
        "deal_total_minor": deal_total,
        "paid_total_minor": paid_total,
        "paid_progress": round(paid_total / deal_total * 100) if deal_total else None,
        "remaining_total_minor": sum(p["deal_amount_minor"] - p["paid_minor"] for p in included),
        "expenses_total_minor": sum(p["expenses_minor"] for p in included),
        "margin_total_minor": sum(p["margin_minor"] for p in included),
        "overdue_count": sum(1 for p in included if p["deadline_state"] == "overdue"),
        "hot_count": sum(1 for p in included if p["deadline_state"] == "hot"),
        "currency": base,
        "excluded_projects": excluded,
    }


def get_project_summary(conn, project_id: int, *, today=None) -> dict:
    today = _resolve_today(today)
    projects.get_project(conn, project_id)

    row = conn.execute(
        "SELECT * FROM v_project_summary WHERE id = ?", (project_id,)
    ).fetchone()
    p = dict(row)
    p["deadline_state"] = dates.deadline_state(p["deadline"], today)

    cats = conn.execute(
        """
        SELECT category_code,
               SUM(amount_minor) AS total_minor,
               COUNT(*)          AS count
        FROM expenses
        WHERE project_id = ?
        GROUP BY category_code
        ORDER BY total_minor DESC, category_code ASC
        """,
        (project_id,),
    ).fetchall()
    p["expenses_by_category"] = [dict(c) for c in cats]

    _apply_payment_fields(p, _payments_map(conn))
    p["payments_total_minor"] = p["paid_minor"]  # имя из спецификации MCP-инструментов
    return p
