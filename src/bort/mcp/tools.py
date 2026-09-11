"""MCP-инструменты «Борта». Тонкие обёртки над services/ — вся логика там.

Каждый инструмент: свои аргументы → JSON. Ошибки домена возвращаются как
{"ok": false, "error": {...}}, не как исключение — агент может осмысленно
среагировать. Суммы: *_minor (канон) + человекочитаемое поле.
"""

from .. import dates, errors, money
from ..services import chats as chats_svc
from ..services import expenses as expenses_svc
from ..services import people as people_svc
from ..services import payments as payments_svc
from ..services import projects as projects_svc
from ..services import summary as summary_svc
from ..services import tasks as tasks_svc
from ..web.api import (
    MONEY_CATEGORY_TOTAL,
    MONEY_EXPENSE,
    MONEY_PAYMENT,
    MONEY_PROJECT,
    MONEY_PROJECT_SUMMARY,
    MONEY_SUMMARY_ROW,
    MONEY_TOTALS,
    with_money,
)


def _conn():
    from .. import db

    return db.connect()


def _ok(data: dict) -> dict:
    return {"ok": True, **data}


def _fail(exc: Exception) -> dict:
    if isinstance(exc, errors.BortError):
        return {"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    return {"ok": False, "error": {"code": "internal", "message": str(exc), "details": {}}}


def _call(fn, **kwargs) -> dict:
    try:
        conn = _conn()
        try:
            return _ok(fn(conn, **kwargs))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — MCP-инструмент не должен ронять транспорт
        return _fail(exc)


def _resolve_project(conn, project_id: int | None = None, name: str | None = None) -> dict:
    """Проект по id или названию. Имя — регистронезависимо; неоднозначность → conflict."""
    if project_id is not None:
        return projects_svc.get_project(conn, project_id)
    if not name or not str(name).strip():
        raise errors.ValidationError("Нужен project_id или name", details={"got": {"project_id": project_id, "name": name}})
    needle = str(name).strip().casefold()
    matches = [
        dict(r)
        for r in conn.execute("SELECT * FROM projects").fetchall()
        if r["name"].casefold() == needle
    ]
    if not matches:
        raise errors.NotFound(f"Проект «{name}» не найден", details={"name": name})
    if len(matches) > 1:
        raise errors.Conflict(
            f"«{name}» — неоднозначное название, уточните по project_id",
            details={"candidates": [{"id": m["id"], "name": m["name"], "status": m["status"]} for m in matches]},
        )
    return matches[0]


def _clean_kwargs(data: dict) -> dict:
    return {k: v for k, v in data.items() if v is not None}


# --- Функции над services (принимают conn первым аргументом) ---


def _project_list(conn, status=None, priority=None, q=None, limit=None):
    result = projects_svc.list_projects(conn, status=status, priority=priority, q=q, limit=limit)
    result["items"] = [with_money(p, MONEY_PROJECT) for p in result["items"]]
    return result


def _project_get(conn, project_id=None, name=None):
    project = _resolve_project(conn, project_id, name)
    tasks = tasks_svc.list_tasks(conn, project["id"])
    listing = expenses_svc.list_expenses(conn, project["id"])
    payments_listing = payments_svc.list_payments(conn, project["id"])
    summary_row = summary_svc.get_project_summary(conn, project["id"])
    return {
        "project": with_money(project, MONEY_PROJECT),
        "tasks": tasks,
        "expenses": [with_money(e, MONEY_EXPENSE) for e in listing["items"]],
        "expenses_total_minor": listing["total_minor"],
        "expenses_total": money.from_minor(listing["total_minor"]),
        "payments": [with_money(p, MONEY_PAYMENT) for p in payments_listing["items"]],
        "payments_total_minor": payments_listing["total_minor"],
        "payments_total": money.from_minor(payments_listing["total_minor"]),
        "payment_progress": summary_row["payment_progress"],
        "chats": chats_svc.list_project_chats(conn, project["id"])["items"],
        "people": people_svc.list_project_people(conn, project["id"])["items"],
    }


def _project_create(conn, name, status=None, priority=None, deal_amount=None, deal_amount_minor=None, currency=None, deadline=None, notes=None):
    if deal_amount is not None and deal_amount_minor is not None:
        raise errors.ValidationError(
            "Переданы обе формы суммы — используйте deal_amount (рубли) или deal_amount_minor (копейки)",
            details={"deal_amount": deal_amount, "deal_amount_minor": deal_amount_minor},
        )
    data = _clean_kwargs(
        {
            "name": name,
            "status": status,
            "priority": priority,
            "currency": currency,
            "deadline": deadline,
            "notes": notes,
            "deal_amount_minor": deal_amount_minor,
        }
    )
    if deal_amount is not None:
        data["deal_amount_minor"] = money.to_minor(deal_amount)
    return {"project": with_money(projects_svc.create_project(conn, data), MONEY_PROJECT)}


def _project_update(conn, project_id=None, name=None, **fields):
    project = _resolve_project(conn, project_id, name)
    if fields.get("deal_amount") is not None:
        fields["deal_amount_minor"] = money.to_minor(fields.pop("deal_amount"))
    updated = projects_svc.update_project(conn, project["id"], _clean_kwargs(fields))
    return {"project": with_money(updated, MONEY_PROJECT)}


def _task_create(conn, project_id=None, project_name=None, title=None, status=None, priority=None, deadline=None, notes=None):
    project = _resolve_project(conn, project_id, project_name)
    task = tasks_svc.create_task(
        conn, project["id"], _clean_kwargs({"title": title, "status": status, "priority": priority, "deadline": deadline, "notes": notes})
    )
    return {"task": task}


def _task_update(conn, task_id=None, **fields):
    task = tasks_svc.update_task(conn, task_id, _clean_kwargs(fields))
    return {"task": task}


def _task_close(conn, task_id=None, outcome="done"):
    task = tasks_svc.close_task(conn, task_id, outcome)
    return {"task": task}


def _expense_add(conn, project_id=None, project_name=None, amount=None, amount_minor=None, spent_on=None, category_code=None, comment=None, currency=None):
    project = _resolve_project(conn, project_id, project_name)
    if amount_minor is None:
        if amount is None:
            raise errors.ValidationError("Нужен amount (рубли) или amount_minor (копейки)")
        amount_minor = money.to_minor(amount)
    data = _clean_kwargs(
        {
            "amount_minor": amount_minor,
            "spent_on": spent_on or dates.today_local().isoformat(),
            "category_code": category_code or "other",
            "comment": comment,
            "currency": currency or project["currency"],
        }
    )
    expense = with_money(expenses_svc.add_expense(conn, project["id"], data), MONEY_EXPENSE)
    fresh = summary_svc.get_project_summary(conn, project["id"])
    return {
        "expense": expense,
        "project_margin_minor": fresh["margin_minor"],
        "project_margin": money.from_minor(fresh["margin_minor"]),
    }


def _summary(conn, scope="open", q=None):
    result = summary_svc.get_summary(conn, scope=scope, q=q)
    result["totals"] = with_money(result["totals"], MONEY_TOTALS)
    result["portfolio_totals"] = with_money(result["portfolio_totals"], MONEY_TOTALS)
    result["projects"] = [with_money(p, MONEY_SUMMARY_ROW) for p in result["projects"]]
    return result


def _project_summary(conn, project_id=None, name=None):
    project = _resolve_project(conn, project_id, name)
    s = with_money(summary_svc.get_project_summary(conn, project["id"]), MONEY_PROJECT_SUMMARY)
    s["expenses_by_category"] = [with_money(c, MONEY_CATEGORY_TOTAL) for c in s["expenses_by_category"]]
    return {"summary": s}


def _chat_attach(conn, project_id=None, project_name=None, chat_id=None, title=None, kind=None, tg_chat_id=None, tg_link=None, note=None):
    project = _resolve_project(conn, project_id, project_name)
    if chat_id is not None:
        chat = chats_svc.attach_chat_to_project(conn, project["id"], int(chat_id), note=note)
    elif title:
        chat = chats_svc.create_chat(conn, _clean_kwargs({"title": title, "kind": kind, "tg_chat_id": tg_chat_id, "tg_link": tg_link}))
        chat = chats_svc.attach_chat_to_project(conn, project["id"], chat["id"], note=note)
    else:
        raise errors.ValidationError("Нужен chat_id либо title нового чата")
    return {"project_id": project["id"], "chat": chat}


def _chat_detach(conn, project_id=None, chat_id=None):
    _resolve_project(conn, project_id)
    chats_svc.detach_chat_from_project(conn, project_id, int(chat_id))
    return {"detached": True, "chat_id": int(chat_id)}


def _chat_list(conn, project_id=None, project_name=None, q=None):
    if project_id is not None or project_name is not None:
        project = _resolve_project(conn, project_id, project_name)
        items = chats_svc.list_project_chats(conn, project["id"])["items"]
        return {"chats": [chats_svc.get_chat(conn, c["id"]) for c in items]}
    result = chats_svc.list_chats(conn, q=q)
    return {"chats": [chats_svc.get_chat(conn, c["id"]) for c in result["items"]]}


def _person_upsert(conn, full_name=None, tg_username=None, notes=None, attach_to_project_id=None, attach_to_project_name=None, attach_to_chat_id=None, role=None):
    person = None
    created = False
    if tg_username:
        for row in conn.execute("SELECT * FROM people").fetchall():
            if row["tg_username"] and row["tg_username"].casefold() == tg_username.casefold():
                person = people_svc.get_person(conn, row["id"])
                break
    if person is None and full_name:
        for row in conn.execute("SELECT * FROM people").fetchall():
            if row["full_name"].casefold() == full_name.casefold():
                person = people_svc.get_person(conn, row["id"])
                break
    data = _clean_kwargs({"full_name": full_name, "tg_username": tg_username, "notes": notes})
    if person is None:
        person = people_svc.create_person(conn, data)
        created = True
    elif data:
        person = people_svc.update_person(conn, person["id"], data)

    attached_project = attached_chat = None
    if attach_to_project_id is not None or attach_to_project_name is not None:
        project = _resolve_project(conn, attach_to_project_id, attach_to_project_name)
        try:
            people_svc.attach_person_to_project(
                conn, project["id"], {"person_id": person["id"], **({"role": role} if role else {})}
            )
        except errors.Conflict:
            if role:
                # Upsert-семантика: привязка есть — обновляем роль
                conn.execute(
                    "UPDATE project_people SET role = ? WHERE project_id = ? AND person_id = ?",
                    (role, project["id"], person["id"]),
                )
                conn.commit()
        attached_project = project["id"]
    if attach_to_chat_id is not None:
        chat_id = int(attach_to_chat_id)
        try:
            chats_svc.add_member(conn, chat_id, {"person_id": person["id"], **({"role": role} if role else {})})
        except errors.Conflict:
            if role:
                conn.execute(
                    "UPDATE chat_members SET role = ? WHERE chat_id = ? AND person_id = ?",
                    (role, chat_id, person["id"]),
                )
                conn.commit()
        attached_chat = chat_id
    person = people_svc.get_person(conn, person["id"])
    return {
        "person": person,
        "created": created,
        "attached_project_id": attached_project,
        "attached_chat_id": attached_chat,
    }


# --- Markdown-ресурс bort://summary ---


def summary_markdown(conn) -> str:
    result = summary_svc.get_summary(conn, scope="open")
    t = result["totals"]
    lines = [
        "# Борт — сводка (открытые проекты)",
        "",
        (
            f"Проектов: {t['projects_count']} · "
            f"Стоимость: {money.format_rub(t['deal_total_minor'])} · "
            f"Затраты: {money.format_rub(t['expenses_total_minor'])} · "
            f"Маржа: {money.format_rub(t['margin_total_minor'])}"
        ),
        f"Просрочено: {t['overdue_count']} · Горит: {t['hot_count']}",
        "",
        "| Проект | Статус | Приоритет | Дедлайн | Дней | Стоимость | Затраты | Маржа |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in result["projects"]:
        dl = dates.days_left(p["deadline"]) if p["deadline"] else None
        lines.append(
            f"| {p['name']} | {p['status']} | {p['priority']} | {p['deadline'] or '—'} "
            f"| {'—' if dl is None else dl} | {money.format_rub(p['deal_amount_minor'])} "
            f"| {money.format_rub(p['expenses_minor'])} | {money.format_rub(p['margin_minor'])} |"
        )
    excluded = t.get("excluded_projects") or []
    if excluded:
        lines.append("")
        lines.append("Вне агрегатов: " + ", ".join(f"{e['name']} ({e['currency']})" for e in excluded))
    return "\n".join(lines)


# --- Регистрация инструментов на MCPServer (mcp 2.x) ---


def register_tools(server) -> None:
    @server.tool(name="bort_project_list", description="Список проектов (краткая форма). Фильтры: status idea|active|paused|closed, priority 1..4 (1 — критичный), поиск q по названию")
    def bort_project_list(status: str | None = None, priority: int | None = None, q: str | None = None, limit: int | None = None) -> dict:
        return _call(_project_list, status=status, priority=priority, q=q, limit=limit)

    @server.tool(name="bort_project_get", description="Проект целиком: задачи, затраты, чаты, люди. Поиск по project_id или точному названию (регистронезависимо)")
    def bort_project_get(project_id: int | None = None, name: str | None = None) -> dict:
        return _call(_project_get, project_id=project_id, name=name)

    @server.tool(name="bort_project_create", description="Создать проект. Сумма: deal_amount в рублях («150 000,50») или deal_amount_minor в копейках")
    def bort_project_create(name: str, status: str | None = None, priority: int | None = None, deal_amount: str | None = None, deal_amount_minor: int | None = None, currency: str | None = None, deadline: str | None = None, notes: str | None = None) -> dict:
        return _call(_project_create, name=name, status=status, priority=priority, deal_amount=deal_amount, deal_amount_minor=deal_amount_minor, currency=currency, deadline=deadline, notes=notes)

    @server.tool(name="bort_project_update", description="Обновить проект (project_id или name); передаются только изменяемые поля")
    def bort_project_update(project_id: int | None = None, name: str | None = None, status: str | None = None, priority: int | None = None, deal_amount: str | None = None, deal_amount_minor: int | None = None, currency: str | None = None, deadline: str | None = None, started_on: str | None = None, finished_on: str | None = None, notes: str | None = None) -> dict:
        return _call(_project_update, project_id=project_id, name=name, status=status, priority=priority, deal_amount=deal_amount, deal_amount_minor=deal_amount_minor, currency=currency, deadline=deadline, started_on=started_on, finished_on=finished_on, notes=notes)

    @server.tool(name="bort_task_create", description="Создать задачу в проекте (project_id или project_name)")
    def bort_task_create(project_id: int | None = None, project_name: str | None = None, title: str | None = None, status: str | None = None, priority: int | None = None, deadline: str | None = None, notes: str | None = None) -> dict:
        return _call(_task_create, project_id=project_id, project_name=project_name, title=title, status=status, priority=priority, deadline=deadline, notes=notes)

    @server.tool(name="bort_task_update", description="Обновить задачу по task_id (передаются только изменяемые поля)")
    def bort_task_update(task_id: int, title: str | None = None, status: str | None = None, priority: int | None = None, deadline: str | None = None, notes: str | None = None) -> dict:
        return _call(_task_update, task_id=task_id, title=title, status=status, priority=priority, deadline=deadline, notes=notes)

    @server.tool(name="bort_task_close", description="Закрыть задачу: outcome 'done' (выполнено) или 'cancelled' (отменено); проставляет closed_at")
    def bort_task_close(task_id: int, outcome: str = "done") -> dict:
        return _call(_task_close, task_id=task_id, outcome=outcome)

    @server.tool(name="bort_expense_add", description="Добавить затрату в проект: amount в рублях («1 200,50») или amount_minor в копейках; spent_on по умолчанию сегодня (BORT_TZ), категория по умолчанию other; возвращает новую маржу проекта")
    def bort_expense_add(project_id: int | None = None, project_name: str | None = None, amount: str | None = None, amount_minor: int | None = None, spent_on: str | None = None, category_code: str | None = None, comment: str | None = None, currency: str | None = None) -> dict:
        return _call(_expense_add, project_id=project_id, project_name=project_name, amount=amount, amount_minor=amount_minor, spent_on=spent_on, category_code=category_code, comment=comment, currency=currency)

    @server.tool(name="bort_summary", description="Сводка по проектам: агрегаты (деньги, счётчики просрочено/горит) и список с состоянием дедлайна. scope: open|active|all")
    def bort_summary(scope: str = "open", q: str | None = None) -> dict:
        return _call(_summary, scope=scope, q=q)

    @server.tool(name="bort_project_summary", description="Сводка одного проекта: деньги, маржа, прогресс задач, разбивка затрат по категориям. По project_id или названию")
    def bort_project_summary(project_id: int | None = None, name: str | None = None) -> dict:
        return _call(_project_summary, project_id=project_id, name=name)

    @server.tool(name="bort_chat_attach", description="Привязать чат к проекту: существующий chat_id или создать новый (title, kind, tg_chat_id, tg_link)")
    def bort_chat_attach(project_id: int | None = None, project_name: str | None = None, chat_id: int | None = None, title: str | None = None, kind: str | None = None, tg_chat_id: str | None = None, tg_link: str | None = None, note: str | None = None) -> dict:
        return _call(_chat_attach, project_id=project_id, project_name=project_name, chat_id=chat_id, title=title, kind=kind, tg_chat_id=tg_chat_id, tg_link=tg_link, note=note)

    @server.tool(name="bort_chat_detach", description="Отвязать чат от проекта (чат остаётся в справочнике)")
    def bort_chat_detach(project_id: int | None = None, chat_id: int | None = None) -> dict:
        return _call(_chat_detach, project_id=project_id, chat_id=chat_id)

    @server.tool(name="bort_chat_list", description="Чаты с участниками и привязками к проектам; с project_id — только чаты проекта")
    def bort_chat_list(project_id: int | None = None, project_name: str | None = None, q: str | None = None) -> dict:
        return _call(_chat_list, project_id=project_id, project_name=project_name, q=q)

    @server.tool(name="bort_person_upsert", description="Создать или обновить человека (ищется по tg_username, затем по имени); можно сразу привязать к проекту и/или чату с ролью")
    def bort_person_upsert(full_name: str | None = None, tg_username: str | None = None, notes: str | None = None, attach_to_project_id: int | None = None, attach_to_project_name: str | None = None, attach_to_chat_id: int | None = None, role: str | None = None) -> dict:
        return _call(_person_upsert, full_name=full_name, tg_username=tg_username, notes=notes, attach_to_project_id=attach_to_project_id, attach_to_project_name=attach_to_project_name, attach_to_chat_id=attach_to_chat_id, role=role)
