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

    # Платежи — одним группированным запросом, без N+1
    paid_map = {
        r["project_id"]: r["paid_minor"]
        for r in conn.execute(
            "SELECT project_id, SUM(amount_minor) AS paid_minor FROM payments GROUP BY project_id"
        ).fetchall()
    }

    items = []
    for row in rows:
        p = dict(row)
        p["deadline_state"] = dates.deadline_state(p["deadline"], today)
        p["paid_minor"] = paid_map.get(p["id"], 0)
        items.append(p)

    if q:
        needle = str(q).casefold()
        items = [p for p in items if needle in p["name"].casefold()]

    if tasks == "open":
        items = [p for p in items if p["tasks_open"] > 0]
    elif tasks == "closed":
        # Все задачи закрыты: задачи ведутся и открытых не осталось
        items = [p for p in items if p["tasks_total"] > 0 and p["tasks_open"] == 0]

    portfolio = get_portfolio_totals(conn, q=q, today=today)
    return {
        "totals": build_totals(items),
        "projects": items,
        "portfolio_totals": portfolio,
    }


def get_portfolio_totals(conn, *, q: str | None = None, today=None) -> dict:
    """Деньги всего портфеля: открытые + закрытые (закрытые не выпадают из маржи).

    Фильтры сводки (scope/tasks/q) на портфель не влияют — только поиск по имени.
    """
    if today is None:
        today = dates.today_local()
    rows = conn.execute(
        f"SELECT * FROM v_project_summary WHERE 1 = 1 {_SUMMARY_ORDER}",
        {"today": today.isoformat()},
    ).fetchall()
    items = []
    paid_map = {
        r["project_id"]: r["paid_minor"]
        for r in conn.execute(
            "SELECT project_id, SUM(amount_minor) AS paid_minor FROM payments GROUP BY project_id"
        ).fetchall()
    }
    for row in rows:
        p = dict(row)
        p["deadline_state"] = dates.deadline_state(p["deadline"], today)
        p["paid_minor"] = paid_map.get(p["id"], 0)
        if q and str(q).casefold() not in p["name"].casefold():
            continue
        items.append(p)
    totals = build_totals(items)
    totals["closed_count"] = sum(1 for p in items if p["status"] == "closed")
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
    return {
        "projects_count": len(included),
        "deal_total_minor": sum(p["deal_amount_minor"] for p in included),
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

    paid = conn.execute(
        "SELECT COALESCE(SUM(amount_minor), 0) AS paid FROM payments WHERE project_id = ?",
        (project_id,),
    ).fetchone()["paid"]
    p["paid_minor"] = paid
    p["payments_total_minor"] = paid  # имя из спецификации MCP-инструментов
    p["remaining_minor"] = p["deal_amount_minor"] - paid
    p["payment_progress"] = (
        round(paid / p["deal_amount_minor"] * 100) if p["deal_amount_minor"] else None
    )
    return p
