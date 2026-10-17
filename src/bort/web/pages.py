"""HTML-страницы и htmx-партиалы. Только транспорт: данные — из services/, SQL здесь запрещён."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import dates, errors, money
from ..services import chats as chats_svc
from ..services import ai_usage as ai_svc
from ..services import beads as beads_svc
from ..services import beads_admin as beads_admin_svc
from ..services import beads_links as beads_links_svc
from ..services import expenses as expenses_svc
from ..services import payments as payments_svc
from ..services import people as people_svc
from ..services import projects as projects_svc
from ..services import summary as summary_svc
from ..services import tasks as tasks_svc
from ..services import work as work_svc
from .api.meta import ENUMS
from .deps import get_conn
from .filters import register_filters

router = APIRouter(tags=["pages"])

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
register_filters(_templates.env)

SCOPE_LABELS = [("open", "Открытые"), ("active", "В работе"), ("closed", "Закрытые"), ("all", "Все")]
_FORM_FIELDS = ("name", "status", "priority", "deal", "currency", "deadline", "started_on", "notes")


def _safe_return_path(raw: str | None) -> str:
    """Внутренний путь для «вернуться назад».

    Whitelist: pathname ровно '/'; query сохраняется как есть
    (вместе с percent-encoding), чтобы не терять фильтры. Схемы, хосты,
    обратные слэши и control-символы отвергаются — ссылка не станет open redirect.
    """
    from urllib.parse import urlsplit

    if not raw:
        return "/"
    s = str(raw).strip()
    if not s:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s) or "\\" in s:
        return "/"
    if s.startswith("//") or "://" in s:
        return "/"
    parts = urlsplit(s)
    if parts.scheme or parts.netloc:
        return "/"
    if parts.path != "/":
        return "/"
    return parts.path + (f"?{parts.query}" if parts.query else "")


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
    request: Request, scope: str = "open", q: str = "", tasks: str = "",
    view: str = "list", conn=Depends(get_conn)
):
    if view not in SUMMARY_VIEWS:
        view = "list"  # страница не падает от чужого параметра; UI-эндпоинты строже
    return _render(request, "summary.html", _board_ctx(conn, scope, q, tasks, view))


@router.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request, conn=Depends(get_conn)):
    return _render(request, "ai.html", ai_svc.dashboard(conn))


BEADS_COLUMNS = [
    ("in_progress", "В работе"), ("open", "Открытые"),
    ("blocked", "Заблокированы"), ("deferred", "Отложены"), ("closed", "Закрытые"),
]


def _beads_context(
    conn, project: str, status: str, priority: str, kind: str, q: str,
    refresh: bool, view: str = "kanban", link_project_id: int | None = None,
    link_error: str | None = None,
) -> dict:
    if status not in {"active", "all", "open", "in_progress", "blocked", "deferred", "closed"}:
        raise errors.ValidationError("Неизвестный статус задач")
    if priority not in {"all", "0", "1", "2", "3", "4"}:
        raise errors.ValidationError("Неизвестный приоритет задач")
    if view not in {"kanban", "list"}:
        raise errors.ValidationError("Неизвестный вид задач")
    all_projects = beads_svc.snapshots(refresh=refresh)
    hidden = beads_admin_svc.hidden_names(conn)
    # Скрытые проекты убраны из списка и подсказок, но снимок и связи не трогаются.
    projects = [item for item in all_projects if item["name"] not in hidden]
    hidden_projects = sorted(name for name in hidden if name in {item["name"] for item in all_projects})
    by_id, by_beads = beads_links_svc.all_links(conn)
    link_target = projects_svc.get_project(conn, link_project_id) if link_project_id is not None else None
    project_names = {item["name"] for item in projects}
    if project and project in hidden:
        project = ""  # проект скрыт: показываем обзор, а не его задачи
    if link_target and not project:
        linked = by_id.get(link_project_id)
        project = linked if linked in project_names else ""
    if project and project not in project_names:
        if refresh:
            project = ""  # исчез после перечитывания: отдать актуальный обзор, не оставлять старые задачи
        else:
            raise errors.NotFound("Доступный проект задач не найден")
    selected = next((item for item in projects if item["name"] == project), None)
    kinds = sorted({item["type"] for item in selected["issues"]}) if selected else []
    if kind != "all" and kind not in kinds:
        raise errors.ValidationError("Неизвестный тип задачи")
    issues = beads_svc.filtered_issues(selected, status=status, priority=priority, kind=kind, q=q) if selected else []
    columns = [
        {"status": code, "label": label, "issues": [item for item in issues if item["status"] == code]}
        for code, label in BEADS_COLUMNS
        if status == "all" or (status == "active" and code != "closed") or code == status
    ]
    current_query = {"status": status, "priority": priority, "type": kind, "q": q, "view": view}
    if selected:
        current_query["project"] = selected["name"]
    if link_project_id is not None:
        current_query["link_project_id"] = link_project_id
    commands = beads_admin_svc.list_commands(conn)
    pending_issues: dict[str, str] = {}
    for command in commands["pending"]:
        issue = command["payload"].get("issue")
        if issue and command["kind"] in ("update", "close", "reopen"):
            pending_issues.setdefault(str(issue), command["kind"])
    return {
        "projects": projects, "selected": selected, "status": status, "view": view,
        "priority": priority, "kind": kind, "q": q, "kinds": kinds,
        "issues": issues, "columns": columns, "link_target": link_target,
        "link_error": link_error, "links": by_beads,
        "source": beads_svc.snapshot_info(),
        "snapshot_mode": beads_svc.is_snapshot_mode(),
        "hidden_projects": hidden_projects,
        "commands": commands,
        "pending_issues": pending_issues,
        "task_types": beads_admin_svc.TASK_TYPES,
        "linked_name": by_id.get(link_project_id),
        "refresh_query": urlencode(current_query),
        "view_urls": {mode: "/tasks?" + urlencode({**current_query, "view": mode}) for mode in ("kanban", "list")},
        "totals": {
            "open": sum(item["counts"].get("open", 0) for item in projects),
            "in_progress": sum(item["counts"].get("in_progress", 0) for item in projects),
            "ready": sum(item["ready"] for item in projects),
        },
    }


@router.get("/beads", include_in_schema=False)
def old_beads_page(request: Request):
    return RedirectResponse("/tasks" + ("?" + request.url.query if request.url.query else ""), status_code=307)


@router.get("/tasks", response_class=HTMLResponse)
def beads_page(
    request: Request, project: str = "", status: str = "active", priority: str = "all",
    kind: str = Query("all", alias="type"), q: str = Query("", max_length=120),
    refresh: bool = False, full: bool = False, view: str = "kanban",
    link_project_id: int | None = None, conn=Depends(get_conn),
):
    # Первый заход мгновенный: htmx загрузит данные отдельно, а без JS доступен ?full=1.
    data = _beads_context(conn, project, status, priority, kind, q, refresh, view, link_project_id) if full or project or link_project_id is not None else None
    return _render(request, "beads.html", {"beads": data})


@router.get("/ui/beads", response_class=HTMLResponse)
def beads_content(
    request: Request, project: str = "", status: str = "active", priority: str = "all",
    kind: str = Query("all", alias="type"), q: str = Query("", max_length=120),
    refresh: bool = False, view: str = "kanban", link_project_id: int | None = None,
    conn=Depends(get_conn),
):
    data = _beads_context(conn, project, status, priority, kind, q, refresh, view, link_project_id)
    return _render(request, "partials/beads_content.html", {"beads": data})


# --- htmx-партиалы: команды к Beads из интерфейса ---


def _beads_state(form) -> dict:
    """Текущее состояние страницы задач из скрытых полей формы."""
    raw_link = str(form.get("link_project_id", "")).strip()
    return {
        "project": str(form.get("project", "")),
        "status": str(form.get("status", "active")),
        "priority": str(form.get("priority", "all")),
        "kind": str(form.get("type", "all")),
        "q": str(form.get("q", "")),
        "view": str(form.get("view", "kanban") or "kanban"),
        "link_project_id": int(raw_link) if raw_link.isdigit() else None,
    }


def _beads_render(
    request: Request, conn, state: dict, *, refresh: bool = False,
    error: str | None = None, status_code: int = 200, new_task_open: bool = False,
    reset_drafts: tuple[str, ...] = (),
) -> HTMLResponse:
    def context(state: dict) -> dict:
        return _beads_context(
            conn, state["project"], state["status"], state["priority"],
            state["kind"], state["q"], refresh, state["view"], state["link_project_id"],
        )

    try:
        data = context(state)
    except errors.NotFound:
        # Команда не прошла (например, проект недоступен): показываем обзор,
        # а не страницу ошибки — сообщение уже лежит в command_error.
        data = context({**state, "project": ""})
    data["command_error"] = error
    data["new_task_open"] = new_task_open
    data["reset_drafts"] = reset_drafts
    return _render(request, "partials/beads_content.html", {"beads": data}, status_code=status_code)


def _beads_enqueue(request, conn, state: dict, *, kind: str, payload: dict) -> HTMLResponse:
    """Общая обёртка: поставить команду в очередь; локально — выполнить сразу."""
    try:
        available = {item["name"] for item in beads_svc.snapshots()}
        beads_admin_svc.enqueue(conn, project=state["project"], kind=kind, payload=payload, available=available)
        if not beads_svc.is_snapshot_mode():
            beads_admin_svc.process_pending_locally(conn)
    except (errors.BortError, ValueError) as exc:
        return _beads_render(
            request, conn, state, error=_err_message(exc),
            status_code=422, new_task_open=(kind == "create"),
        )
    return _beads_render(
        request, conn, state, refresh=True,
        reset_drafts=("new-task",) if kind == "create" else (),
    )


@router.post("/tasks/issues/create", response_class=HTMLResponse)
async def beads_issue_create(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    payload = {
        "title": form.get("task_title", ""),
        "type": form.get("task_type", "task"),
        "priority": form.get("task_priority", 2),
        "description": form.get("task_description", ""),
    }
    return _beads_enqueue(request, conn, state, kind="create", payload=payload)


@router.post("/tasks/issues/update", response_class=HTMLResponse)
async def beads_issue_update(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    payload = {
        "issue": form.get("issue", ""),
        "status": form.get("issue_status", ""),
        "priority": form.get("issue_priority", ""),
    }
    return _beads_enqueue(request, conn, state, kind="update", payload=payload)


@router.post("/tasks/issues/close", response_class=HTMLResponse)
async def beads_issue_close(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    payload = {"issue": form.get("issue", ""), "reason": form.get("reason", "")}
    return _beads_enqueue(request, conn, state, kind="close", payload=payload)


@router.post("/tasks/issues/reopen", response_class=HTMLResponse)
async def beads_issue_reopen(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    payload = {"issue": form.get("issue", "")}
    return _beads_enqueue(request, conn, state, kind="reopen", payload=payload)


@router.post("/tasks/sync", response_class=HTMLResponse)
async def beads_sync(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    return _beads_enqueue(request, conn, state, kind="sync", payload={})


@router.post("/tasks/commands/{command_id}/dismiss", response_class=HTMLResponse)
async def beads_command_dismiss(request: Request, command_id: int, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    try:
        beads_admin_svc.dismiss(conn, command_id)
    except (errors.BortError, ValueError) as exc:
        return _beads_render(request, conn, state, error=_err_message(exc), status_code=422)
    return _beads_render(request, conn, state)


@router.post("/tasks/projects/hide", response_class=HTMLResponse)
async def beads_hide_project(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    name = str(form.get("name", ""))
    try:
        beads_admin_svc.hide_project(conn, name)
    except (errors.BortError, ValueError) as exc:
        return _beads_render(request, conn, state, error=_err_message(exc), status_code=422)
    if state["project"] == name:
        state["project"] = ""  # скрыли открытый проект — показываем обзор
    return _beads_render(request, conn, state)


@router.post("/tasks/projects/unhide", response_class=HTMLResponse)
async def beads_unhide_project(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    state = _beads_state(form)
    try:
        beads_admin_svc.unhide_project(conn, str(form.get("name", "")))
    except (errors.BortError, ValueError) as exc:
        return _beads_render(request, conn, state, error=_err_message(exc), status_code=422)
    return _beads_render(request, conn, state)


@router.post("/tasks/links/{project_id}", response_class=HTMLResponse)
async def link_beads_project(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    name = str(form.get("beads_project", ""))
    try:
        beads_links_svc.set_link(conn, project_id, name, {item["name"] for item in beads_svc.snapshots(refresh=True)})
    except (errors.ValidationError, errors.Conflict) as exc:
        data = _beads_context(conn, "", "active", "all", "all", "", False, link_project_id=project_id, link_error=exc.message)
        return _render(request, "beads.html", {"beads": data}, status_code=409 if isinstance(exc, errors.Conflict) else 422)
    return RedirectResponse("/tasks?" + urlencode({"project": name}), status_code=303)


@router.post("/tasks/links/{project_id}/remove")
def unlink_beads_project(project_id: int, conn=Depends(get_conn)):
    beads_links_svc.remove_link(conn, project_id)
    return RedirectResponse(f"/tasks?link_project_id={project_id}", status_code=303)


@router.api_route(
    "/ui/ai/providers/{provider}/refresh", methods=["GET", "POST"], response_class=HTMLResponse
)
async def ai_provider_refresh(request: Request, provider: str, conn=Depends(get_conn)):
    refresher = ai_svc.REFRESHERS.get(provider)
    if refresher is None:
        raise errors.NotFound(
            f"AI-провайдер {provider!r} не поддерживается", details={"provider": provider}
        )
    if provider in ("codex", "qwen"):
        # POST (кнопка) — заявка MacBook-агенту; GET (автообновление карточки) — только перечитать снимок.
        snapshot = refresher(conn, request_refresh=request.method == "POST")
    else:
        snapshot = refresher(conn)
    return _render(
        request,
        f"partials/ai_{provider}_card.html",
        {"provider": snapshot},
    )


@router.post("/ui/ai/refresh-all", response_class=HTMLResponse)
async def ai_refresh_all(request: Request, conn=Depends(get_conn)):
    result = ai_svc.refresh_all(conn)
    return _render(request, "partials/ai_provider_grid.html", {"providers": result["providers"]})


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(
    request: Request, project_id: int, return_to: str = "", conn=Depends(get_conn)
):
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
            "return_to": _safe_return_path(return_to),
        },
    )


@router.delete("/ui/projects/{project_id}")
async def ui_project_delete(request: Request, project_id: int, conn=Depends(get_conn)):
    """Удалить проект из UI вместе с каскадами (задачи, затраты, оплаты, связи).

    htmx шлёт DELETE с form-encoded телом, юнит-вызовы — query. Цель возврата
    санитизируется `_safe_return_path`; htmx получает HX-Redirect, обычный
    переход — 303. Несуществующий проект → NotFound → 404 (глобальный handler).
    """
    form = await request.form()

    def pick(key: str) -> str:
        value = form.get(key)
        if value is None or str(value) == "":
            return str(request.query_params.get(key, ""))
        return str(value)

    target = _safe_return_path(pick("return_to"))
    projects_svc.delete_project(conn, project_id)
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


# --- htmx-партиалы: сводка ---


SUMMARY_VIEWS = ("list", "kanban")


def _board_ctx(conn, scope: str, q: str, tasks: str = "", view: str = "list") -> dict:
    if view not in SUMMARY_VIEWS:
        raise errors.ValidationError(
            "Неизвестный вид сводки", details={"allowed": list(SUMMARY_VIEWS)}
        )
    scope = "open"  # фильтр разделов с экрана убран: сводка показывает работу и архив
    try:
        data = summary_svc.get_summary(conn, scope=scope, q=q or None, tasks=tasks or None)
    except errors.ValidationError:
        q, tasks = "", ""
        data = summary_svc.get_summary(conn, scope=scope)
    closed = summary_svc.get_summary(conn, scope="closed", q=q or None, tasks=tasks or None)
    projects = data["projects"]
    return {
        **data,
        "attention": summary_svc.get_attention(conn),
        "scope": scope or "open",
        "q": q or "",
        "tasks_filter": tasks or "",
        "view": view,
        "scope_labels": SCOPE_LABELS,
        "closed_projects": closed["projects"],
        "closed_totals": closed["totals"],
        "beads_links": beads_links_svc.all_links(conn)[0],
        "manual_order": projects_svc.has_manual_order(conn),
        # Колонки канбана: рабочие статусы проекта; архив — свёрнутый блок ниже.
        "kanban_columns": [
            {"code": code, "label": title, "projects": [p for p in projects if p["status"] == code]}
            for code, title in ENUMS["project_statuses"]
            if code != "closed"
        ],
    }


@router.get("/ui/projects", response_class=HTMLResponse)
def ui_project_table(
    request: Request,
    scope: str = "open",
    q: str = "",
    tasks: str = "",
    view: str = "list",
    conn=Depends(get_conn),
):
    ctx = _board_ctx(conn, scope, q, tasks, view)
    ctx["state_oob"] = True
    return _render(request, "partials/summary_projects.html", ctx)


@router.post("/ui/projects/reorder", response_class=HTMLResponse)
async def ui_reorder_projects(request: Request, conn=Depends(get_conn)):
    """Переставить проекты сводки: список id в новом порядке (drag-and-drop списка)."""
    form = await request.form()
    raw = [str(item) for item in form.getlist("ids")]
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    view = str(form.get("view", "list") or "list")
    q = str(form.get("q", ""))
    tasks = str(form.get("tasks", ""))
    try:
        projects_svc.reorder_projects(conn, raw)
    except (errors.BortError, ValueError):
        ctx = _board_ctx(conn, "open", q, tasks, view if view in SUMMARY_VIEWS else "list")
        return _render(request, "partials/summary_projects.html", ctx, status_code=422)
    ctx = _board_ctx(conn, "open", q, tasks, view if view in SUMMARY_VIEWS else "list")
    return _render(request, "partials/summary_projects.html", ctx)


@router.post("/ui/projects/reorder/reset", response_class=HTMLResponse)
async def ui_reset_project_order(request: Request, conn=Depends(get_conn)):
    """Вернуть автосортировку сводки. htmx шлёт параметры в теле формы."""
    form = await request.form()

    def pick(key: str, default: str = "") -> str:
        value = form.get(key)
        return str(value) if value not in (None, "") else str(request.query_params.get(key, default))

    view = pick("view") or "list"
    q = pick("q")
    tasks = pick("tasks")
    projects_svc.reset_order(conn)
    ctx = _board_ctx(conn, "open", q, tasks, view if view in SUMMARY_VIEWS else "list")
    return _render(request, "partials/summary_projects.html", ctx)


@router.get("/ui/projects/new", response_class=HTMLResponse)
def ui_project_form(request: Request, view: str = "list"):
    view = view if view in SUMMARY_VIEWS else "list"
    return _render(
        request,
        "partials/project_form.html",
        {"form_error": None, "form_values": {}, "view": view},
    )


@router.post("/ui/projects", response_class=HTMLResponse)
async def ui_create_project(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    scope = str(form.get("scope", "open"))
    q = str(form.get("q", ""))
    tasks = str(form.get("tasks", ""))
    view = str(form.get("view", "list") or "list")
    view = view if view in SUMMARY_VIEWS else "list"
    values = {k: str(form.get(k, "")) for k in _FORM_FIELDS}
    try:
        projects_svc.create_project(conn, _project_form_data(form))
    except (errors.BortError, ValueError) as e:
        ctx = _board_ctx(conn, scope, q, tasks, view)
        ctx.update({"form_error": _err_message(e), "form_values": values, "form_open": True})
        return _render(request, "partials/project_form.html", ctx)
    ctx = _board_ctx(conn, scope, q, tasks, view)
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
        "view": "list",
        "board_target": "#tasks-block",
        "kanban_statuses": KANBAN_SEQUENCE,
        "form_error": error,
        "form_values": values or {},
    }


@router.get("/ui/projects/{project_id}/tasks", response_class=HTMLResponse)
def ui_tasks_list(
    request: Request,
    project_id: int,
    q: str = "",
    view: str = "",
    task_q: str = Query("", alias="task-q"),
    conn=Depends(get_conn),
):
    if not q and task_q:
        q = task_q  # legacy-имя поиска из старых разметок/ссылок
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
    task = tasks_svc.get_task(conn, task_id)
    if status:
        try:
            tasks_svc.update_task(conn, task_id, {"status": status})
        except (errors.BortError, ValueError):
            pass
    q = str(form.get("q", ""))
    view = str(form.get("view", "list") or "list")
    return _render_task_action(
        request,
        conn,
        task,
        view=view,
        q=q,
        project_id=task["project_id"],
    )


def _task_board_target(view: str) -> str:
    return "#tasks-block"


def _render_task_action(
    request: Request,
    conn,
    task: dict,
    *,
    view: str,
    q: str,
    project_id: int | None = None,
) -> HTMLResponse:
    """Ответ после правки/удаления/статуса задачи: та же доска, те же фильтры."""
    view = view if view in ("kanban", "list") else "list"
    pid = project_id if project_id is not None else task["project_id"]
    if view == "kanban":
        return _render(request, "partials/kanban_block.html", _tasks_ctx(conn, pid, q))
    return _render(request, "partials/tasks_block.html", _tasks_ctx(conn, pid, q))


def _task_edit_ctx(conn, task_id: int, q: str, view: str) -> dict:
    task = tasks_svc.get_task(conn, task_id)
    view = view if view in ("kanban", "list") else "list"
    return {
        "t": task,
        "q": q or "",
        "view": view,
        "board_target": _task_board_target(view),
    }


@router.get("/ui/tasks/{task_id}/edit", response_class=HTMLResponse)
def ui_task_edit_form(
    request: Request,
    task_id: int,
    q: str = "",
    view: str = "list",
    conn=Depends(get_conn),
):
    return _render(
        request, "partials/task_edit_form.html", _task_edit_ctx(conn, task_id, q, view)
    )


@router.post("/ui/tasks/{task_id}/edit", response_class=HTMLResponse)
async def ui_task_edit(request: Request, task_id: int, conn=Depends(get_conn)):
    form = await request.form()
    view = str(form.get("view", "list") or "list")
    q = str(form.get("q", ""))
    data: dict = {}
    if str(form.get("title", "")).strip():
        data["title"] = form["title"]
    if form.get("priority"):
        try:
            data["priority"] = int(form["priority"])
        except (TypeError, ValueError):
            pass
    if "deadline" in form:
        data["deadline"] = str(form.get("deadline", "")).strip() or None
    if form.get("status"):
        data["status"] = form["status"]
    task = tasks_svc.get_task(conn, task_id)
    try:
        task = tasks_svc.update_task(conn, task_id, data)
    except (errors.BortError, ValueError):
        pass  # невалидные данные — перерисовываем текущее состояние
    return _render_task_action(
        request,
        conn,
        task,
        view=view,
        q=q,
        project_id=task["project_id"],
    )


@router.delete("/ui/tasks/{task_id}", response_class=HTMLResponse)
async def ui_task_delete(request: Request, task_id: int, conn=Depends(get_conn)):
    # htmx шлёт DELETE с form-encoded телом, юнит-вызовы — query. Поддерживаем оба,
    # иначе фильтр доски теряется после удаления (реальный браузерный баг).
    form = await request.form()

    def pick(key: str) -> str:
        value = form.get(key)
        if value is None or str(value) == "":
            return str(request.query_params.get(key, ""))
        return str(value)

    q = pick("q")
    view = pick("view") or "list"
    task = tasks_svc.get_task(conn, task_id)
    tasks_svc.delete_task(conn, task_id)
    return _render_task_action(
        request,
        conn,
        task,
        view=view,
        q=q,
        project_id=task["project_id"],
    )


# --- htmx-партиалы: оплата проекта ---


def _payments_ctx(
    conn,
    project_id: int,
    error: str | None = None,
    values: dict | None = None,
    money_oob: bool = False,
) -> dict:
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
        # Панель денег наверху карточки едет тем же ответом: платёж меняет её числа.
        # На первой отрисовке страницы OOB не нужен — там панель и так своя.
        "p": project,
        "s": summary_svc.get_project_summary(conn, project_id),
        "money_oob": money_oob,
    }


# --- htmx-партиалы: инлайн-правка «Стоимости» проекта ---


def _human_amount(minor: int) -> str:
    """Копейки → человеческая форма для поля ввода: «150 000» / «150 000,50», ноль → «0»."""
    minor = int(minor or 0)
    units = f"{minor // 100:,}".replace(",", " ")
    return units if minor % 100 == 0 else f"{units},{minor % 100:02d}"


def _money_ctx(conn, project_id: int) -> dict:
    project = projects_svc.get_project(conn, project_id)
    return {
        "project_id": project_id,
        "p": project,
        "s": summary_svc.get_project_summary(conn, project_id),
        "money_oob": False,
    }


def _deal_edit_ctx(conn, project_id: int, form_error: str | None = None, values: dict | None = None) -> dict:
    project = projects_svc.get_project(conn, project_id)
    return {
        "project_id": project_id,
        "p": project,
        "value": _human_amount(project["deal_amount_minor"]),
        "form_error": form_error,
        "form_values": values or {},
    }


@router.get("/ui/projects/{project_id}/deal/edit", response_class=HTMLResponse)
def ui_project_deal_edit(request: Request, project_id: int, conn=Depends(get_conn)):
    return _render(request, "partials/project_deal_edit.html", _deal_edit_ctx(conn, project_id))


@router.get("/ui/projects/{project_id}/money", response_class=HTMLResponse)
def ui_project_money(request: Request, project_id: int, conn=Depends(get_conn)):
    """«Отмена» инлайн-правки: вернуть обычную панель денег."""
    return _render(request, "partials/project_money.html", _money_ctx(conn, project_id))


@router.post("/ui/projects/{project_id}/deal", response_class=HTMLResponse)
async def ui_project_deal_save(request: Request, project_id: int, conn=Depends(get_conn)):
    form = await request.form()
    raw = str(form.get("deal", "")).strip()
    try:
        minor = 0 if not raw else money.to_minor(raw)
        if minor < 0:
            raise errors.ValidationError("Стоимость не может быть отрицательной")
        projects_svc.update_project(conn, project_id, {"deal_amount_minor": minor})
    except (errors.BortError, ValueError) as e:
        ctx = _deal_edit_ctx(conn, project_id, _err_message(e), {"deal": raw})
        return _render(request, "partials/project_deal_edit.html", ctx)
    return _render(request, "partials/project_money.html", _money_ctx(conn, project_id))


@router.get("/ui/projects/{project_id}/payments", response_class=HTMLResponse)
def ui_payments_block(request: Request, project_id: int, conn=Depends(get_conn)):
    return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id, money_oob=True))


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
        return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id, _err_message(e), values, money_oob=True))
    return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id, money_oob=True))


@router.delete("/ui/projects/{project_id}/payments/{payment_id}", response_class=HTMLResponse)
def ui_delete_payment(request: Request, project_id: int, payment_id: int, conn=Depends(get_conn)):
    try:
        payments_svc.delete_payment(conn, payment_id)
    except errors.BortError:
        pass
    return _render(request, "partials/payments_block.html", _payments_ctx(conn, project_id, money_oob=True))


def _expenses_block_ctx(
    conn,
    project_id: int,
    error: str | None = None,
    values: dict | None = None,
    money_oob: bool = False,
) -> dict:
    listing = expenses_svc.list_expenses(conn, project_id)
    s = summary_svc.get_project_summary(conn, project_id)
    return {
        "project_id": project_id,
        # Затрата двигает маржу — панель денег наверху едет тем же ответом.
        "p": projects_svc.get_project(conn, project_id),
        "s": s,
        "money_oob": money_oob,
        "expenses": listing["items"],
        "expenses_total_minor": listing["total_minor"],
        "expenses_by_category": s["expenses_by_category"],
        "categories": expenses_svc.list_categories(conn)["items"],
        # Без этого разбивка по категориям роняла htmx-перерисовку блока затрат
        # (шаблон зовёт cat_titles, а его отдавала только страница проекта).
        "cat_titles": {
            c["code"]: c["title_ru"]
            for c in expenses_svc.list_categories(conn, include_inactive=True)["items"]
        },
        "today_iso": _today_iso(),
        "form_error": error,
        "form_values": values or {},
    }


def _today_iso() -> str:
    from .. import dates

    return dates.today_local().isoformat()


@router.get("/ui/projects/{project_id}/expenses", response_class=HTMLResponse)
def ui_expenses_block(request: Request, project_id: int, conn=Depends(get_conn)):
    return _render(request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id, money_oob=True))


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
            request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id, _err_message(e), values, money_oob=True)
        )
    return _render(request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id, money_oob=True))


@router.delete("/ui/projects/{project_id}/expenses/{expense_id}", response_class=HTMLResponse)
def ui_delete_expense(request: Request, project_id: int, expense_id: int, conn=Depends(get_conn)):
    try:
        expenses_svc.delete_expense(conn, expense_id)
    except errors.BortError:
        pass
    return _render(request, "partials/expenses_block.html", _expenses_block_ctx(conn, project_id, money_oob=True))


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
        summary_svc._apply_payment_fields(row, summary_svc._payments_map(conn))
        row["deadline_state"] = dates.deadline_state(row["deadline"])
        row["next_task"] = summary_svc._next_tasks_map(conn, [project_id]).get(project_id)
    closed_rows = [p for p in data["projects"] if p["status"] == "closed"]
    return {
        "p": row,
        "dl_suggest": _dl_suggest(),
        "oob": False,
        "closed_projects": closed_rows,
        "beads_links": beads_links_svc.all_links(conn)[0],
    }


def _dl_suggest() -> dict:
    from datetime import timedelta

    from ..dates import today_local

    today = today_local()
    return {n: (today + timedelta(days=n)).isoformat() for n in (1, 7, 14, 30)}


@router.post("/ui/projects/{project_id}/fields", response_class=HTMLResponse)
async def ui_update_project_fields(request: Request, project_id: int, conn=Depends(get_conn)):
    """Инлайн-правка статуса/приоритета/дедлайна: POST-форма от select'а в строке
    сводки или в шапке карточки. Возвращает обновлённую строку / таблицу / шапку."""
    form = await request.form()
    data: dict = {}
    if form.get("status"):
        data["status"] = str(form["status"])
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
    view = str(form.get("view", ""))
    q = str(form.get("q", ""))
    tasks = str(form.get("tasks", ""))
    if view == "card":
        return await _render_project_head(request, conn, project_id)
    if view == "kanban":
        # Канбан сводки: правка статуса перестраивает колонки, деньги и внимание — OOB.
        ctx = _board_ctx(conn, "open", q, tasks, "kanban")
        ctx.update({"state_oob": True, "portfolio_oob": True, "attention_oob": True})
        return _render(request, "partials/summary_projects.html", ctx)
    if "status" in data:
        # Статус двигает проект между разделами «Открытые» и «Закрытые» и меняет
        # счётчики просрочек — одной строкой не обойтись, перерисовываем таблицу.
        ctx = _board_ctx(conn, "open", q, tasks, "list")
        ctx.update({"state_oob": True, "portfolio_oob": True, "attention_oob": True})
        return _render(request, "partials/summary_projects.html", ctx)
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


# --- Работа: справочник постоянных проектов ---
#
# Раздел независим от денег «Сводки» и от Beads: своя таблица work_projects,
# свои итоги. Форма и список живут в отдельных боксах; создание/правка
# возвращают форму, а обновлённый список и итоги едут OOB-свопом.

_WORK_FORM_FIELDS = (
    "name", "client", "role", "format", "monthly",
    "currency", "status", "started_on", "url", "notes",
)


def _work_ctx(conn, **overrides) -> dict:
    ctx = {
        "items": work_svc.list_work_projects(conn),
        "totals": work_svc.work_totals(conn),
        "format_labels": dict(work_svc.WORK_FORMATS),
        "work_formats": work_svc.WORK_FORMATS,
        "work_statuses": work_svc.WORK_STATUSES,
        "form_error": None,
        "form_values": {},
        "work_totals_oob": False,
        "saved": False,
        "w": None,
        "action": "/ui/work",
    }
    ctx.update(overrides)
    return ctx


def _work_form_values(form) -> dict:
    return {k: str(form.get(k, "")) for k in _WORK_FORM_FIELDS}


def _work_values_from_record(w: dict) -> dict:
    minor = int(w["monthly_amount_minor"] or 0)
    units = f"{minor // 100:,}".replace(",", " ")
    amount = units if minor % 100 == 0 else f"{units},{minor % 100:02d}"
    return {
        "name": w["name"],
        "client": w["client"] or "",
        "role": w["role"] or "",
        "format": w["format"],
        "monthly": amount,
        "currency": w["currency"],
        "status": w["status"],
        "started_on": w["started_on"] or "",
        "url": w["url"] or "",
        "notes": w["notes"] or "",
    }


def _work_form_data(form) -> dict:
    """Данные формы → payload. Все поля, кроме имени, всегда явные: пустое
    значение у необязательного поля очищает его при правке."""
    data: dict = {}
    if str(form.get("name", "")).strip():
        data["name"] = form["name"]
    for key in ("client", "role", "notes", "url"):
        raw = str(form.get(key, "")).strip()
        data[key] = raw or None
    data["format"] = str(form.get("format") or "other")
    data["status"] = str(form.get("status") or "active")
    data["currency"] = str(form.get("currency") or "RUB")
    amount = str(form.get("monthly", "")).strip()
    data["monthly_amount_minor"] = money.to_minor(amount) if amount else 0
    data["started_on"] = str(form.get("started_on", "")).strip() or None
    return data


@router.get("/work", response_class=HTMLResponse)
def work_page(request: Request, conn=Depends(get_conn)):
    return _render(request, "work.html", _work_ctx(conn))


@router.get("/ui/work/new", response_class=HTMLResponse)
def ui_work_new(request: Request, conn=Depends(get_conn)):
    return _render(request, "partials/work_form.html", _work_ctx(conn))


@router.post("/ui/work", response_class=HTMLResponse)
async def ui_work_create(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    values = _work_form_values(form)
    try:
        work_svc.create_work_project(conn, _work_form_data(form))
    except (errors.BortError, ValueError) as e:
        return _render(
            request, "partials/work_form.html",
            _work_ctx(conn, form_error=_err_message(e), form_values=values),
        )
    return _render(request, "partials/work_form.html", _work_ctx(conn, saved=True))


@router.get("/ui/work/{work_id}/edit", response_class=HTMLResponse)
def ui_work_edit_form(request: Request, work_id: int, conn=Depends(get_conn)):
    w = work_svc.get_work_project(conn, work_id)
    return _render(
        request, "partials/work_form.html",
        _work_ctx(conn, w=w, action=f"/ui/work/{work_id}", form_values=_work_values_from_record(w)),
    )


@router.post("/ui/work/{work_id}", response_class=HTMLResponse)
async def ui_work_update(request: Request, work_id: int, conn=Depends(get_conn)):
    form = await request.form()
    values = _work_form_values(form)
    action = f"/ui/work/{work_id}"
    try:
        work_svc.update_work_project(conn, work_id, _work_form_data(form))
    except (errors.BortError, ValueError) as e:
        w = work_svc.get_work_project(conn, work_id)
        return _render(
            request, "partials/work_form.html",
            _work_ctx(conn, w=w, action=action, form_error=_err_message(e), form_values=values),
        )
    w = work_svc.get_work_project(conn, work_id)
    return _render(
        request, "partials/work_form.html", _work_ctx(conn, w=w, action=action, saved=True)
    )


@router.post("/ui/work/{work_id}/status", response_class=HTMLResponse)
async def ui_work_status(request: Request, work_id: int, conn=Depends(get_conn)):
    form = await request.form()
    status = form.get("status")
    if status:
        try:
            work_svc.update_work_project(conn, work_id, {"status": status})
        except (errors.BortError, ValueError):
            pass  # невалидный статус — перерисовываем текущее состояние
    return _render(request, "partials/work_table.html", _work_ctx(conn, work_totals_oob=True))


@router.delete("/ui/work/{work_id}", response_class=HTMLResponse)
async def ui_work_delete(request: Request, work_id: int, conn=Depends(get_conn)):
    try:
        work_svc.delete_work_project(conn, work_id)
    except errors.BortError:
        pass
    return _render(request, "partials/work_table.html", _work_ctx(conn, work_totals_oob=True))
