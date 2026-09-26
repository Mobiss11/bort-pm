"""Регрессии независимой браузерной приёмки (Chromium, fixture-БД).

Покрывают: непустые option у enum-селектов и дефолты, метки полей для адаптивной
списковой раскладки, доступность правки/удаления в канбане, сохранение фильтров
при удалении реальными htmx-параметрами (form-encoded body).

Все CRUD — на fixture-БД (tests/conftest.py). Production не трогаем.
"""

import re

OPTION_RE = re.compile(r'<option value="([^"]*)"[^>]*>([^<]*)</option>')
STATUS_CODES = ["todo", "in_progress", "review", "done", "cancelled"]


def _blank_options(html: str) -> list[tuple[str, str]]:
    return [(v, t) for v, t in OPTION_RE.findall(html) if not v.strip() and not t.strip()]


def _options(html: str) -> list[tuple[str, str]]:
    return OPTION_RE.findall(html)


def _selected_values(html: str) -> list[str]:
    return [
        m.group(1)
        for m in re.finditer(r'<option value="([^"]*)"[^>]*\bselected\b[^>]*>', html)
    ]


def _mk_project(client, name="Проект", **extra):
    payload = {"name": name, "status": "active"}
    payload.update(extra)
    return client.post("/api/v1/projects", json=payload).json()


def _mk_task(client, project_id, title, **extra):
    payload = {"title": title}
    payload.update(extra)
    return client.post(f"/api/v1/projects/{project_id}/tasks", json=payload).json()


# ---------- 1. enum options: непустые значения/подписи и дефолты ----------


def test_project_new_form_enum_options_nonblank(client):
    html = client.get("/ui/projects/new").text
    assert _blank_options(html) == []
    status = re.search(r'<select name="status">.*?</select>', html, re.S).group(0)
    assert [v for v, _ in _options(status)] == ["idea", "active", "paused", "closed"]
    prio = re.search(r'<select name="priority">.*?</select>', html, re.S).group(0)
    assert _selected_values(prio) == ["2"]


def test_project_page_and_task_form_enum_options_nonblank(client):
    p = _mk_project(client, "Карточка")
    _mk_task(client, p["id"], "ALPHA", status="review", priority=2)
    html = client.get(f"/projects/{p['id']}").text
    assert _blank_options(html) == []
    # форма создания задачи: приоритет по умолчанию «3»
    form = re.search(
        r'<form class="inline-form compact"[^>]*hx-post="/ui/projects/\d+/tasks".*?</form>',
        html,
        re.S,
    ).group(0)
    prio = re.search(r'<select name="priority">.*?</select>', form, re.S).group(0)
    assert _selected_values(prio) == ["3"]
    # статус задачи в строке: ровно 5 непустых русских подписей
    sel = re.search(r'<select class="status-select.*?</select>', html, re.S).group(0)
    assert [v for v, _ in _options(sel)] == STATUS_CODES
    assert "review" in _selected_values(sel)


def test_project_task_create_uses_submitted_priority(client):
    p = _mk_project(client, "Создание")
    r = client.post(
        f"/ui/projects/{p['id']}/tasks",
        data={"title": "P1", "priority": "1", "deadline": "", "q": ""},
    )
    assert r.status_code == 200
    task = client.get(f"/api/v1/projects/{p['id']}/tasks").json()
    item = next(t for t in task["items"] if t["title"] == "P1")
    assert item["priority"] == 1


# ---------- 2. адаптивная списочная раскладка ----------


def test_project_list_rows_have_data_labels(client):
    p = _mk_project(client, "Метки")
    _mk_task(client, p["id"], "ALPHA")
    html = client.get(f"/projects/{p['id']}").text
    for label in ("Статус", "Задача", "Приоритет", "Дедлайн"):
        assert f'data-label="{label}"' in html


# ---------- 3. правка/удаление доступны из канбана ----------


def test_project_kanban_has_edit_and_delete_controls(client):
    p = _mk_project(client, "ПроектКанбанПравка")
    _mk_task(client, p["id"], "ALPHA")
    html = client.get(f"/projects/{p['id']}").text
    kanban = re.search(r'<div id="kanban-block">.*?</div>\s*</div>', html, re.S).group(0)
    assert 'hx-delete="/ui/tasks/' in kanban
    assert "/edit" in kanban


# ---------- 4. удаление/правка реальными htmx-параметрами сохраняют фильтр ----------


def test_delete_with_htmx_form_body_keeps_filter(client):
    p = _mk_project(client, "УдалениеФильтр")
    t1 = _mk_task(client, p["id"], "ALPHA")
    _mk_task(client, p["id"], "BETA")
    # htmx шлёт DELETE с form-encoded телом (не query): именно так терялся фильтр
    r = client.request(
        "DELETE",
        f"/ui/tasks/{t1['id']}",
        data={"view": "list", "q": "ALPHA"},
    )
    assert r.status_code == 200
    assert 'id="tasks-block"' in r.text
    assert 'class="task-row' not in r.text  # удалённая не вернулась строкой
    assert "BETA" not in r.text  # q-фильтр сохранён: BETA тоже не показан
    assert client.get(f"/api/v1/tasks/{t1['id']}").status_code == 404


def test_edit_with_htmx_form_body_keeps_filter(client):
    p = _mk_project(client, "ПравкаФильтр")
    t1 = _mk_task(client, p["id"], "ALPHA")
    _mk_task(client, p["id"], "BETA")
    r = client.post(
        f"/ui/tasks/{t1['id']}/edit",
        data={
            "title": "ALPHA-2",
            "priority": "1",
            "deadline": "",
            "status": "todo",
            "view": "list",
            "q": "ALPHA",
        },
    )
    assert r.status_code == 200
    assert 'id="tasks-block"' in r.text and "ALPHA-2" in r.text
    assert "BETA" not in r.text
    assert client.get(f"/api/v1/tasks/{t1['id']}").json()["title"] == "ALPHA-2"
