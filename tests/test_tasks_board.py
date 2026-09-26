"""Канбан и список задач на карточке проекта: честный первичный рендер,
безопасный return_to назад на сводку.
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
    # путь, отличный от сводки, больше не принимается — возвращаемся на '/'
    r = client.get(f"/projects/{p['id']}", params={"return_to": "/tasks?view=list&q=x"})
    assert 'href="/tasks' not in r.text
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
