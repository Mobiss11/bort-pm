"""Глобальные задачи: список И канбан с честным первичным рендером,
фильтры сохраняются после всех действий и в URL, безопасный return_to.
"""

import re


def _mk_project(client, name="Доска", **extra):
    payload = {"name": name, "status": "active", "priority": 2}
    payload.update(extra)
    r = client.post("/api/v1/projects", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _mk_task(client, project_id, title, **extra):
    payload = {"title": title}
    payload.update(extra)
    r = client.post(f"/api/v1/projects/{project_id}/tasks", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_global_kanban_initial_render(client):
    p = _mk_project(client, "Доска А")
    t1 = _mk_task(client, p["id"], "Написать отчёт")
    _mk_task(client, p["id"], "Позвонить клиенту", status="in_progress")
    html = client.get("/tasks").text
    assert 'id="global-kanban"' in html
    assert "Написать отчёт" in html  # карточки отрисованы сразу, не по требованию
    assert f'data-task-id="{t1["id"]}"' in html
    assert "Доска А" in html  # проект на карточке
    assert "col-todo" in html and "col-in_progress" in html


def test_global_list_initial_render(client):
    p = _mk_project(client, "Список А")
    t = _mk_task(client, p["id"], "Задача в списке")
    html = client.get("/tasks?view=list").text
    assert 'id="global-list"' in html
    assert "Задача в списке" in html
    assert "Список А" in html
    # переключатель видов присутствует и сохраняет фильтры в URL
    assert "/tasks?view=kanban" in html
    assert "/tasks?view=list" in html


def test_board_partial_respects_view(client):
    p = _mk_project(client, "Част")
    _mk_task(client, p["id"], "Частичная задача")
    kan = client.get("/ui/tasks/board").text
    assert 'id="tasks-board"' in kan and 'id="global-kanban"' in kan
    lst = client.get("/ui/tasks/board?view=list").text
    assert 'id="tasks-board"' in lst and 'id="global-list"' in lst
    assert "Частичная задача" in lst


def test_project_filter_and_q_on_global_list(client):
    p1 = _mk_project(client, "Проект Один")
    p2 = _mk_project(client, "Проект Два")
    _mk_task(client, p1["id"], "Задача альфа")
    _mk_task(client, p2["id"], "Задача бета")
    html = client.get("/tasks?view=list&project_id=%d" % p2["id"]).text
    assert "Задача бета" in html
    assert "Задача альфа" not in html
    # фильтры названы и применённые видны
    assert "Проект: все" in html
    assert "Показаны" in html


def test_status_change_from_list_preserves_filters(client):
    p = _mk_project(client, "Статусный")
    t1 = _mk_task(client, p["id"], "Сделать альфу")
    _mk_task(client, p["id"], "Сделать бету")
    r = client.post(
        f"/ui/tasks/{t1['id']}/status",
        data={"status": "in_progress", "view": "global_list", "q": "альфу", "project_id": str(p["id"])},
    )
    assert r.status_code == 200
    assert 'id="tasks-board"' in r.text
    assert "Сделать альфу" in r.text
    assert "Сделать бету" not in r.text  # q-фильтр сохранён после действия
    # фактический статус изменился
    assert client.get(f"/api/v1/tasks/{t1['id']}").json()["status"] == "in_progress"


def test_status_change_in_kanban_keeps_filters(client):
    p = _mk_project(client, "КанбанКью")
    t1 = _mk_task(client, p["id"], "Кью задача")
    _mk_task(client, p["id"], "Другая задача")
    r = client.post(
        f"/ui/tasks/{t1['id']}/status",
        data={"status": "review", "view": "global", "q": "Кью"},
    )
    assert r.status_code == 200
    assert 'id="global-kanban"' in r.text
    assert "Кью задача" in r.text
    assert "Другая задача" not in r.text
    # кнопки-альтернативы drag-drop на месте с aria
    assert "aria-label" in r.text


def test_create_task_from_list_preserves_view_and_filters(client):
    p = _mk_project(client, "Создание")
    r = client.post(
        "/ui/tasks",
        data={"project_id": str(p["id"]), "title": "Свежая задача", "view": "list", "q": "Свежая"},
    )
    assert r.status_code == 200
    assert 'id="tasks-board"' in r.text and "Свежая задача" in r.text
    assert 'value="Свежая"' in r.text  # q остался в поиске
    assert f'value="{p["id"]}" selected' in r.text  # проектный фильтр остался


def test_global_list_empty_state_with_cta(client):
    p = _mk_project(client, "ПустоПроект")
    r = client.get("/tasks?view=list").text
    assert "Задач нет" in r
    # с фильтром проекта — честный пустой стейт
    html = client.get(f"/tasks?view=list&project_id={p['id']}").text
    assert "Нет задач" in html
    assert "+ Задача" in html  # CTA доступен


def test_project_kanban_initial_load(client):
    p = _mk_project(client, "ПроектКанбан")
    t = _mk_task(client, p["id"], "Первоначальная задача")
    html = client.get(f"/projects/{p['id']}").text
    assert 'id="kanban-block"' in html
    assert "Первоначальная задача" in html
    assert "kanban-placeholder" not in html


def test_return_to_whitelist(client):
    p = _mk_project(client, "Возврат")
    r = client.get(f"/projects/{p['id']}", params={"return_to": "https://evil.com"})
    assert "evil.com" not in r.text
    r = client.get(f"/projects/{p['id']}", params={"return_to": "//evil.com"})
    assert "evil.com" not in r.text
    r = client.get(f"/projects/{p['id']}", params={"return_to": "/tasks?view=list&q=x"})
    assert 'href="/tasks?view=list&amp;q=x"' in r.text
    # дефолт — сводка
    r = client.get(f"/projects/{p['id']}")
    assert 'href="/"' in r.text


def test_project_row_next_step_and_cta(client):
    p1 = _mk_project(client, "Строчный")
    _mk_task(client, p1["id"], "Первая строчная задача")
    p2 = _mk_project(client, "СтрочныйПустой")
    html = client.get("/").text
    assert 'data-label="Следующий шаг"' in html
    assert "Первая строчная задача" in html
    assert "+ Задача" in html
    # CTA ровно у проекта без незакрытых задач — в таблице открытых
    open_tbody = re.search(r'<tbody id="projects-tbody">(.*?)</tbody>', html, re.S).group(1)
    assert open_tbody.count("+ Задача") == 1
    rows = re.findall(r'<tr class="project-row.*?</tr>', open_tbody, re.S)
    row_with_task = next(r for r in rows if ">Строчный</a>" in r)
    row_empty = next(r for r in rows if f"/projects/{p2['id']}" in r)
    assert "+ Задача" not in row_with_task
    assert "+ Задача" in row_empty


def test_summary_filters_visible_in_url_and_state(client):
    p = _mk_project(client, "Фильтровый")
    _mk_task(client, p["id"], "Открытая таска")
    client.post("/api/v1/projects", json={"name": "ЗакрытыйФильтр", "status": "closed"})
    html = client.get("/", params={"tasks": "open", "q": "Фильтровый"}).text
    assert "Показаны" in html
    assert "поиск «Фильтровый»" in html
    assert "с открытыми задачами" in html
    # таблица отфильтрована, закрытая секция отдельна
    assert "Фильтровый" in html
