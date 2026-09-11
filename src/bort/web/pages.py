"""HTML-страницы и htmx-партиалы. Только транспорт: данные — из services/, SQL здесь запрещён."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import config, db, dates, errors, money
from ..services import chats as chats_svc
from ..services import expenses as expenses_svc
from ..services import payments as payments_svc
from ..services import people as people_svc
from ..services import projects as projects_svc
from ..services import summary as summary_svc
from ..services import tasks as tasks_svc
from .api.meta import ENUMS
from .deps import get_conn
from .filters import register_filters

router = APIRouter(tags=["pages"])

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
register_filters(_templates.env)

SCOPE_LABELS = [("open", "Открытые"), ("active", "В работе"), ("closed", "Закрытые"), ("all", "Все")]
_FORM_FIELDS = ("name", "status", "priority", "deal", "currency", "deadline", "started_on", "notes")


def _render(request: Request, name: str, ctx: dict, status_code: int = 200) -> HTMLResponse:
    ctx.setdefault("enums", ENUMS)
    ctx.setdefault("dl_suggest", _dl_suggest())
    return _templates.TemplateResponse(
        request=request, name=name, context=ctx, status_code=status_code
    )


def _err_message(exc) -> str:
    return exc.message if isinstance(exc, errors.BortError) else str(exc)


def _project_form_data(form) -> dict:
    data: dict = {}
    if str(form.get("name", "")).strip():
        data["name"] = form["name"]
    if form.get("status"):
        data["status"] = form["status"]
    if form.get("priority"):
        data["priority"] = int(form["priority"])
    deal = str(form.get("deal", "")).strip()
    if deal:
        data["deal_amount_minor"] = money.to_minor(deal)
    if form.get("currency"):
        data["currency"] = form["currency"]
    for key in ("deadline", "started_on", "finished_on"):
        if form.get(key):
            data[key] = form[key]
    if str(form.get("notes", "")).strip():
        data["notes"] = str(form["notes"]).strip()
    return data


# --- Страницы ---


@router.get("/", response_class=HTMLResponse)
def summary_page(
    request: Request, scope: str = "open", q: str = "", tasks: str = "", conn=Depends(get_conn)
):
    try:
        data = summary_svc.get_summary(conn, scope=scope, q=q or None, tasks=tasks or None)
    except errors.ValidationError:
        scope, q, tasks = "open", "", ""
        data = summary_svc.get_summary(conn, scope=scope)
    closed = summary_svc.get_summary(conn, scope="closed", q=q or None)
    return _render(
        request,
        "summary.html",
        {
            **data,
            "scope": scope,
            "q": q or "",
            "tasks_filter": tasks or "",
            "scope_labels": SCOPE_LABELS,
            "closed_projects": closed["projects"],
            "closed_totals": closed["totals"],
            "active_nav": "summary",
        },
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int, conn=Depends(get_conn)):
    project = projects_svc.get_project(conn, project_id)
    s = summary_svc.get_project_summary(conn, project_id)
    tasks = tasks_svc.list_tasks(conn, project_id)
    listing = expenses_svc.list_expenses(conn, project_id)
    cat_titles = {
        c["code"]: c["title_ru"] for c in expenses_svc.list_categories(conn, include_inactive=True)["items"]
    }

    attached_chats = chats_svc.list_project_chats(conn, project_id)["items"]
    chats_full = []
    for c in attached_chats:
        full = chats_svc.get_chat(conn, c["id"])
        full["link_note"] = c["link_note"]
        chats_full.append(full)
    attached_ids = {c["id"] for c in attached_chats}
    free_chats = [c for c in chats_svc.list_chats(conn)["items"] if c["id"] not in attached_ids]

    attached_people = people_svc.list_project_people(conn, project_id)["items"]
    people_ids = {p["person_id"] for p in attached_people}
    free_people = [p for p in people_svc.list_people(conn)["items"] if p["id"] not in people_ids]

    # Контекст блока «Оплата» (payments_block.html инклюдится прямо в project.html)
    ctx = _payments_ctx(conn, project_id)

    return _render(
        request,
        "project.html",
        {
            "p": project,
            "s": s,
            "tasks": tasks,
            "tasks_all_count": len(tasks),
            "kanban_statuses": KANBAN_SEQUENCE,
            "expenses": listing["items"],
            "expenses_total_minor": listing["total_minor"],
            "expenses_by_category": s["expenses_by_category"],
            "cat_titles": cat_titles,
            "chats": chats_full,
            "free_chats": free_chats,
            "attached_people": attached_people,
            "free_people": free_people,
            **ctx,
            "form_error": None,
            "form_values": {},
            "active_nav": "summary",
        },
    )


@router.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request, q: str = "", conn=Depends(get_conn)):
    data = chats_svc.list_chats(conn, q=q or None)
    return _render(request, "chats.html", {**data, "q": q or "", "active_nav": "chats"})


def _chats_page_ctx(conn, q: str = "") -> dict:
    items = chats_svc.list_chats(conn, q=q or None)["items"]
    all_people = people_svc.list_people(conn)["items"]
    chats = []
    for c in items:
        full = chats_svc.get_chat(conn, c["id"])
        member_ids = {m["person_id"] for m in full["members"]}
        chats.append(
            {
                "chat": full,
                "free_people": [p for p in all_people if p["id"] not in member_ids],
            }
        )
    return {"chats": chats, "q": q or ""}


@router.get("/people", response_class=HTMLResponse)
def people_page(request: Request, q: str = "", conn=Depends(get_conn)):
    data = people_svc.list_people(conn, q=q or None)
    return _render(request, "people.html", {**data, "q": q or "", "active_nav": "people"})


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, conn=Depends(get_conn)):
    return _render(
        request,
        "settings.html",
        {
            "categories": expenses_svc.list_categories(conn, include_inactive=True)["items"],
            "tz": config.tz_name(),
            "db_path": config.db_path(),
            "schema_version": db.schema_version(conn),
            "base_currency": "RUB",
            "active_nav": "settings",
        },
    )


# --- htmx-партиалы: сводка ---


def _board_ctx(conn, scope: str, q: str, tasks: str = "") -> dict:
    data = summary_svc.get_summary(conn, scope=scope or "open", q=q or None, tasks=tasks or None)
    closed = summary_svc.get_summary(conn, scope="closed", q=q or None)
    return {
        **data,
        "scope": scope or "open",
        "q": q or "",
        "tasks_filter": tasks or "",
        "scope_labels": SCOPE_LABELS,
        "closed_projects": closed["projects"],
        "closed_totals": closed["totals"],
    }


@router.get("/ui/projects", response_class=HTMLResponse)
def ui_project_table(
    request: Request,
    scope: str = "open",
    q: str = "",
    tasks: str = "",
    conn=Depends(get_conn),
):
    return _render(request, "partials/project_table.html", _board_ctx(conn, scope, q, tasks))


@router.get("/ui/projects/new", response_class=HTMLResponse)
def ui_project_form(request: Request):
    return _render(request, "partials/project_form.html", {"form_error": None, "form_values": {}})


@router.post("/ui/projects", response_class=HTMLResponse)
async def ui_create_project(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    scope = str(form.get("scope", "open"))
    q = str(form.get("q", ""))
    tasks = str(form.get("tasks", ""))
    values = {k: str(form.get(k, "")) for k in _FORM_FIELDS}
    try:
        projects_svc.create_project(conn, _project_form_data(form))
    except (errors.BortError, ValueError) as e:
        ctx = _board_ctx(conn, scope, q, tasks)
        ctx.update({"form_error": _err_message(e), "form_values": values, "form_open": True})
        return _render(request, "partials/project_form.html", ctx)
    ctx = _board_ctx(conn, scope, q, tasks)
    ctx.update({"form_error": None, "form_values": {}, "form_open": True, "created": True, "oob": True})
    return _render(request, "partials/project_form.html", ctx)


# --- htmx-партиалы: карточка проекта ---


KANBAN_SEQUENCE = ["todo", "in_progress", "review", "done"]


def _tasks_ctx(
    conn,
    project_id: int,
    q: str = "",
    error: str | None = None,
    values: dict | None = None,
) -> dict:
    tasks_list = tasks_svc.list_tasks(conn, project_id)
    q = (q or "").strip()
    if q:
        needle = q.casefold()
        tasks_list = [t for t in tasks_list if needle in t["title"].casefold()]
    return {
        "project_id": project_id,
        "tasks": tasks_list,
        "tasks_all_count": len(tasks_svc.list_tasks(conn, project_id)),
        "q": q,
        "kanban_statuses": KANBAN_SEQUENCE,
        "form_error": error,
        "form_values": values or {},
    }


@router.get("/ui/projects/{project_id}/tasks", response_class=HTMLResponse)
def ui_tasks_list(
    request: Request, project_id: int, q: str = "", view: str = "", conn=Depends(get_conn)
):
    ctx = _tasks_ctx(conn, project_id, q)
    if view == "kanban":
        # Ленивая загрузка канбана: содержимое колонок внутрь #kanban-block
        return _render(request, "partials/kanban_columns.html", ctx)
    # Поиск по задачам: обновляем список и канбан (out-of-band) — поле поиска не перерисовывается
    ctx["oob_kanban"] = True
    return _render(request, "partials/tasks_list.html", ctx)


@router.post("/ui/projects/{project_id}/tasks", response_class=HTMLResponse)
async def ui_create_task(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    q = str(form.get("q", ""))
    values = {k: str(form.get(k, "")) for k in ("title", "priority", "deadline", "notes")}
    data: dict = {}
    if str(form.get("title", "")).strip():
        data["title"] = form["title"]
    if form.get("priority"):
        data["priority"] = int(form["priority"])
    if form.get("deadline"):
        data["deadline"] = form["deadline"]
    if str(form.get("notes", "")).strip():
        data["notes"] = str(form["notes"]).strip()
    try:
        tasks_svc.create_task(conn, project_id, data)
    except (errors.BortError, ValueError) as e:
        return _render(request, "partials/tasks_block.html", _tasks_ctx(conn, project_id, q, _err_message(e), values))
    return _render(request, "partials/tasks_block.html", _tasks_ctx(conn, project_id, q))


@router.post("/ui/tasks/{task_id}/status", response_class=HTMLResponse)
async def ui_task_status(request: Request, task_id: int, conn=Depends(get_conn)):
    form = await request.form()
    status = form.get("status")
    view = form.get("view", "list")
    task = tasks_svc.get_task(conn, task_id)
    if status:
        try:
            tasks_svc.update_task(conn, task_id, {"status": status})
        except (errors.BortError, ValueError):
            pass
    q = str(form.get("q", ""))
    if view == "kanban":
        return _render(request, "partials/kanban_block.html", _tasks_ctx(conn, task["project_id"], q))
    if view == "global":
        pid_raw = str(form.get("project_id", "") or "")
        pid = int(pid_raw) if pid_raw.isdigit() else None
        return _render(
            request,
            "partials/kanban_global.html",
            _global_board_ctx(conn, q=q, project_id=pid),
        )
    return _render(request, "partials/tasks_block.html", _tasks_ctx(conn, task["project_id"], q))


# --- htmx-партиалы: оплата проекта ---


def _payments_ctx(conn, project_id: int, error: str | None = None, values: dict | None = None) -> dict:
    project = projects_svc.get_project(conn, project_id)
    listing = payments_svc.list_payments(conn, project_id)
    deal = project["deal_amount_minor"]
    paid = listing["total_minor"]
    return {
        "project_id": project_id,
        "payments": listing["items"],
        "paid_total_minor": paid,
        "deal_amount_minor": deal,
        "remaining_minor": deal - paid,
        "payment_progress": (round(paid / deal * 100) if deal else None),
        "kind_labels": payments_svc.KIND_LABELS,
        "today_iso": _today_iso(),
        "form_error": error,
        "form_values": values or {},
    }


@router.get("/ui/projects/{project_id}/payments", response_class=HTMLResponse)
def ui_payments_block(request: Request, project_id: int, conn=Depends(get_conn)):
    return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id))


@router.post("/ui/projects/{project_id}/payments", response_class=HTMLResponse)
async def ui_add_payment(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    values = {k: str(form.get(k, "")) for k in ("amount", "kind", "paid_on", "comment")}
    try:
        data: dict = {}
        amount = str(form.get("amount", "")).strip()
        if amount:
            data["amount_minor"] = money.to_minor(amount)
        if form.get("kind"):
            data["kind"] = form["kind"]
        if form.get("paid_on"):
            data["paid_on"] = form["paid_on"]
        if str(form.get("comment", "")).strip():
            data["comment"] = str(form["comment"]).strip()
        payments_svc.add_payment(conn, project_id, data)
    except (errors.BortError, ValueError) as e:
        return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id, _err_message(e), values))
    return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id))


@router.delete("/ui/projects/{project_id}/payments/{payment_id}", response_class=HTMLResponse)
def ui_delete_payment(request: Request, project_id: int, payment_id: int, conn=Depends(get_conn)):
    try:
        payments_svc.delete_payment(conn, payment_id)
    except errors.BortError:
        pass
    return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id))


def _expenses_block_ctx(conn, project_id: int, error: str | None = None, values: dict | None = None) -> dict:
    listing = expenses_svc.list_expenses(conn, project_id)
    s = summary_svc.get_project_summary(conn, project_id)
    return {
        "project_id": project_id,
        "expenses": listing["items"],
        "expenses_total_minor": listing["total_minor"],
        "expenses_by_category": s["expenses_by_category"],
        "categories": expenses_svc.list_categories(conn)["items"],
        "today_iso": _today_iso(),
        "form_error": error,
        "form_values": values or {},
    }


def _today_iso() -> str:
    from .. import dates

    return dates.today_local().isoformat()


@router.get("/ui/projects/{project_id}/expenses", response_class=HTMLResponse)
def ui_expenses_block(request: Request, project_id: int, conn=Depends(get_conn)):
    return _render(request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id))


@router.post("/ui/projects/{project_id}/expenses", response_class=HTMLResponse)
async def ui_create_expense(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    values = {k: str(form.get(k, "")) for k in ("amount", "category_code", "spent_on", "comment")}
    data: dict = {}
    amount = str(form.get("amount", "")).strip()
    if amount:
        data["amount_minor"] = money.to_minor(amount)
    if form.get("category_code"):
        data["category_code"] = form["category_code"]
    if form.get("spent_on"):
        data["spent_on"] = form["spent_on"]
    if str(form.get("comment", "")).strip():
        data["comment"] = str(form["comment"]).strip()
    try:
        expenses_svc.add_expense(conn, project_id, data)
    except (errors.BortError, ValueError) as e:
        return _render(
            request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id, _err_message(e), values)
        )
    return _render(request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id))


@router.delete("/ui/projects/{project_id}/expenses/{expense_id}", response_class=HTMLResponse)
def ui_delete_expense(request: Request, project_id: int, expense_id: int, conn=Depends(get_conn)):
    try:
        expenses_svc.delete_expense(conn, expense_id)
    except errors.BortError:
        pass
    return _render(request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id))


def _chats_block_ctx(conn, project_id: int, error: str | None = None) -> dict:
    attached = chats_svc.list_project_chats(conn, project_id)["items"]
    chats_full = []
    for c in attached:
        full = chats_svc.get_chat(conn, c["id"])
        full["link_note"] = c["link_note"]
        chats_full.append(full)
    attached_ids = {c["id"] for c in attached}
    return {
        "project_id": project_id,
        "chats": chats_full,
        "free_chats": [c for c in chats_svc.list_chats(conn)["items"] if c["id"] not in attached_ids],
        "form_error": error,
    }


@router.get("/ui/projects/{project_id}/chats", response_class=HTMLResponse)
def ui_chats_block(request: Request, project_id: int, conn=Depends(get_conn)):
    return _render(request, "partials/chats_block.html", _chats_block_ctx(conn, project_id))


@router.post("/ui/projects/{project_id}/chats", response_class=HTMLResponse)
async def ui_attach_chat(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    note = str(form.get("note", "")).strip() or None
    try:
        if form.get("chat_id"):
            chats_svc.attach_chat_to_project(conn, project_id, int(form["chat_id"]), note=note)
        elif str(form.get("title", "")).strip():
            chat = chats_svc.create_chat(
                conn,
                {
                    "title": form["title"],
                    **({"kind": form["kind"]} if form.get("kind") else {}),
                    **({"tg_chat_id": form["tg_chat_id"]} if form.get("tg_chat_id") else {}),
                    **({"tg_link": form["tg_link"]} if form.get("tg_link") else {}),
                },
            )
            chats_svc.attach_chat_to_project(conn, project_id, chat["id"], note=note)
        else:
            raise errors.ValidationError("Выберите чат из справочника или заполните название нового")
    except (errors.BortError, ValueError) as e:
        return _render(request, "partials/chats_block.html", _chats_block_ctx(conn, project_id, _err_message(e)))
    return _render(request, "partials/chats_block.html", _chats_block_ctx(conn, project_id))


@router.delete("/ui/projects/{project_id}/chats/{chat_id}", response_class=HTMLResponse)
def ui_detach_chat(request: Request, project_id: int, chat_id: int, conn=Depends(get_conn)):
    try:
        chats_svc.detach_chat_from_project(conn, project_id, chat_id)
    except errors.BortError:
        pass
    return _render(request, "partials/chats_block.html", _chats_block_ctx(conn, project_id))


def _people_block_ctx(conn, project_id: int, error: str | None = None) -> dict:
    attached = people_svc.list_project_people(conn, project_id)["items"]
    people_ids = {p["person_id"] for p in attached}
    return {
        "project_id": project_id,
        "attached_people": attached,
        "free_people": [p for p in people_svc.list_people(conn)["items"] if p["id"] not in people_ids],
        "form_error": error,
    }


@router.get("/ui/projects/{project_id}/people", response_class=HTMLResponse)
def ui_people_block(request: Request, project_id: int, conn=Depends(get_conn)):
    return _render(request, "partials/people_block.html", _people_block_ctx(conn, project_id))


@router.post("/ui/projects/{project_id}/people", response_class=HTMLResponse)
async def ui_attach_person(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    data: dict = {}
    if form.get("person_id"):
        data["person_id"] = int(form["person_id"])
    if str(form.get("role", "")).strip():
        data["role"] = str(form["role"]).strip()
    try:
        people_svc.attach_person_to_project(conn, project_id, data)
    except (errors.BortError, ValueError) as e:
        return _render(request, "partials/people_block.html", _people_block_ctx(conn, project_id, _err_message(e)))
    return _render(request, "partials/people_block.html", _people_block_ctx(conn, project_id))


@router.delete("/ui/projects/{project_id}/people/{person_id}", response_class=HTMLResponse)
def ui_detach_person(request: Request, project_id: int, person_id: int, conn=Depends(get_conn)):
    try:
        people_svc.detach_person_from_project(conn, project_id, person_id)
    except errors.BortError:
        pass
    return _render(request, "partials/people_block.html", _people_block_ctx(conn, project_id))


@router.post("/ui/projects/{project_id}/notes", response_class=HTMLResponse)
async def ui_save_notes(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    notes = str(form.get("notes", ""))
    projects_svc.update_project(conn, project_id, {"notes": notes})
    return _render(
        request,
        "partials/notes_block.html",
        {"project_id": project_id, "notes": notes, "saved_at": _now_hhmm()},
    )


def _now_hhmm() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%d.%m %H:%M")


def _field_row_ctx(conn, project_id: int) -> dict:
    """Контекст одной строки таблицы проектов после обновления полей."""
    data = summary_svc.get_summary(conn, scope="all", q=None, tasks=None)
    row = next((item for item in data["projects"] if item["id"] == project_id), None)
    if row is None:  # проект скрыт текущими фильтрами сводки — собираем строку вручную
        row = projects_svc.get_project(conn, project_id)
        row["paid_minor"] = sum(
            pay["amount_minor"] for pay in payments_svc.list_payments(conn, project_id)["items"]
        )
        row["deadline_state"] = dates.deadline_state(row["deadline"])
    closed_rows = [p for p in data["projects"] if p["status"] == "closed"]
    return {
        "p": row,
        "dl_suggest": _dl_suggest(),
        "oob": False,
        "closed_projects": closed_rows,
    }


def _dl_suggest() -> dict:
    from datetime import timedelta

    from ..dates import today_local

    today = today_local()
    return {n: (today + timedelta(days=n)).isoformat() for n in (1, 7, 14, 30)}


@router.post("/ui/projects/{project_id}/fields", response_class=HTMLResponse)
async def ui_update_project_fields(request: Request, project_id: int, conn=Depends(get_conn)):
    """Инлайн-правка приоритета/дедлайна: POST-форма от select'а в строке сводки
    или в шапке карточки. Возвращает обновлённую строку таблицы / шапку карточки."""
    form = await request.form()
    data: dict = {}
    if form.get("priority"):
        try:
            data["priority"] = int(form["priority"])
        except (TypeError, ValueError):
            pass
    if "deadline" in form:  # пустая строка = снять дедлайн
        raw = str(form.get("deadline", "")).strip()
        data["deadline"] = raw or None
    try:
        projects_svc.update_project(conn, project_id, data)
    except (errors.BortError, ValueError):
        pass  # невалидное значение — просто перерисовываем текущее состояние
    if str(form.get("view", "")) == "card":
        return await _render_project_head(request, conn, project_id)
    return _render(request, "partials/project_row.html", _field_row_ctx(conn, project_id))


async def _render_project_head(request: Request, conn, project_id: int) -> HTMLResponse:
    """Шапка карточки проекта (project_head.html) после правки полей."""
    project = projects_svc.get_project(conn, project_id)
    s = summary_svc.get_project_summary(conn, project_id)
    return _render(
        request,
        "partials/project_head.html",
        {"p": project, "s": s, "enums": ENUMS, "dl_suggest": _dl_suggest()},
    )


# --- Глобальный канбан задач (/tasks) ---


@router.get("/tasks", response_class=HTMLResponse)
def tasks_global_page(
    request: Request, q: str = "", project_id: int | None = None, conn=Depends(get_conn)
):
    ctx = _global_board_ctx(conn, q=q, project_id=project_id)
    ctx["active_nav"] = "tasks"
    return _render(request, "tasks_global.html", ctx)


@router.post("/ui/tasks", response_class=HTMLResponse)
async def ui_create_task_global(request: Request, conn=Depends(get_conn)):
    """Создание задачи из формы в глобальном канбане."""
    form = await request.form()
    q = str(form.get("q", ""))
    project_id_raw = str(form.get("project_id", "") or "")
    project_id = int(project_id_raw) if project_id_raw.isdigit() else None
    values = {k: str(form.get(k, "")) for k in ("title", "priority", "deadline")}
    values["project_id"] = project_id_raw
    data: dict = {}
    if str(form.get("title", "")).strip():
        data["title"] = form["title"]
    if form.get("priority"):
        try:
            data["priority"] = int(form["priority"])
        except (TypeError, ValueError):
            pass
    if form.get("deadline"):
        data["deadline"] = form["deadline"]
    try:
        if project_id is None:
            raise errors.ValidationError("Выберите проект для задачи")
        tasks_svc.create_task(conn, project_id, data)
    except (errors.BortError, ValueError) as e:
        ctx = _global_board_ctx(conn, q=q, project_id=project_id)
        ctx.update({"form_error": _err_message(e), "form_values": values})
        return _render(request, "partials/kanban_global.html", ctx)
    return _render(request, "partials/kanban_global.html", _global_board_ctx(conn, q=q, project_id=project_id))


def _global_board_ctx(conn, q: str = "", project_id: int | None = None) -> dict:
    return {
        "tasks": tasks_svc.list_all_tasks(conn, q=q or None, project_id=project_id),
        "projects": projects_svc.list_projects(conn, limit=None)["items"],
        "q": q or "",
        "project_id": project_id,
        "kanban_statuses": KANBAN_SEQUENCE,
        "form_error": None,
        "form_values": {},
    }


@router.get("/ui/tasks/board", response_class=HTMLResponse)
def ui_global_board(
    request: Request, q: str = "", project_id: int | None = None, conn=Depends(get_conn)
):
    return _render(
        request, "partials/kanban_global.html", _global_board_ctx(conn, q=q, project_id=project_id)
    )


# --- htmx-партиалы: справочники ---


@router.get("/ui/chats", response_class=HTMLResponse)
def ui_chats_list(request: Request, q: str = "", conn=Depends(get_conn)):
    return _render(request, "partials/chats_page_list.html", _chats_page_ctx(conn, q))


@router.post("/ui/chats/{chat_id}/members", response_class=HTMLResponse)
async def ui_add_chat_member(request: Request, chat_id: int, conn=Depends(get_conn)):
    form = await request.form()
    if form.get("person_id"):
        try:
            chats_svc.add_member(
                conn, chat_id, {"person_id": int(form["person_id"]), "role": form.get("role") or None}
            )
        except (errors.BortError, ValueError):
            pass
    return _render(request, "partials/chats_page_list.html", _chats_page_ctx(conn))


@router.delete("/ui/chats/{chat_id}/members/{person_id}", response_class=HTMLResponse)
def ui_remove_chat_member(request: Request, chat_id: int, person_id: int, conn=Depends(get_conn)):
    try:
        chats_svc.remove_member(conn, chat_id, person_id)
    except errors.BortError:
        pass
    return _render(request, "partials/chats_page_list.html", _chats_page_ctx(conn))


@router.post("/ui/settings/backup", response_class=HTMLResponse)
def ui_backup(request: Request):
    import subprocess

    script = Path(__file__).resolve().parents[3] / "scripts" / "backup.sh"
    try:
        proc = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=60
        )
        if proc.returncode == 0 and proc.stdout.strip():
            message = f"Бэкап создан: {proc.stdout.strip()}"
        else:
            message = f"Ошибка бэкапа: {proc.stderr.strip()[:300] or 'нет вывода'}"
    except Exception as e:  # noqa: BLE001 — показываем любую проблему пользователю
        message = f"Ошибка бэкапа: {e}"
    return _render(request, "partials/backup_result.html", {"backup_message": message})


@router.post("/ui/chats", response_class=HTMLResponse)
async def ui_create_chat(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    data: dict = {"title": form.get("title", "")}
    if form.get("kind"):
        data["kind"] = form["kind"]
    if form.get("tg_chat_id"):
        data["tg_chat_id"] = form["tg_chat_id"]
    if form.get("tg_link"):
        data["tg_link"] = form["tg_link"]
    try:
        chats_svc.create_chat(conn, data)
    except (errors.BortError, ValueError):
        pass
    return RedirectResponse("/chats", status_code=303)


@router.post("/ui/people", response_class=HTMLResponse)
async def ui_create_person(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    data: dict = {"full_name": form.get("full_name", "")}
    if form.get("tg_username"):
        data["tg_username"] = form["tg_username"]
    try:
        people_svc.create_person(conn, data)
    except (errors.BortError, ValueError):
        pass
    return RedirectResponse("/people", status_code=303)
