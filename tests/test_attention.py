"""Первый экран сводки: что требует внимания — только реальные действия.

- просроченные ОТКРЫТЫЕ проекты (закрытые не подаются как требующие работы);
- фактическая следующая незакрытая задача проекта и её проект;
- порядок бакетов urgent / started / other, недатированные задачи не исчезают;
- проект без незакрытых задач → явный CTA «добавить задачу», без выдуманных задач.
"""

import re
from datetime import date

import pytest

from bort.services import projects, summary, tasks as tasks_svc

TODAY = date(2026, 9, 14)


def _project(conn, name, **extra):
    data = {"name": name, "status": "active", "priority": 2}
    data.update(extra)
    return projects.create_project(conn, data)


def _task(conn, project_id, title, **extra):
    data = {"title": title}
    data.update(extra)
    return tasks_svc.create_task(conn, project_id, data)


@pytest.fixture()
def _seed(conn):
    a = _project(conn, "Критичный", priority=1)
    _task(conn, a["id"], "Недатированная критичная", priority=1)

    b = _project(conn, "Вработе")
    _task(conn, b["id"], "Начатая", priority=3, status="in_progress")

    c = _project(conn, "ЗакрытПросроч", status="closed", priority=1, deadline="2026-09-01")
    _task(conn, c["id"], "Хвост закрытого")

    d = _project(conn, "Пустой")

    e = _project(conn, "ПросрочОткрытый", deadline="2026-09-01")  # overdue, открыт
    return {"a": a, "b": b, "c": c, "d": d, "e": e}


def test_attention_buckets_truthful_and_ordered(conn, _seed):
    att = summary.get_attention(conn, today=TODAY)
    items = att["items"]
    bucket_order = {"urgent": 0, "started": 1, "other": 2}
    buckets = [i["bucket"] for i in items]
    assert buckets == sorted(buckets, key=lambda b: bucket_order[b])
    titles = [i["task"]["title"] for i in items]
    # только реальные незакрытые задачи открытых проектов
    assert "Хвост закрытого" not in titles
    by_title = {i["task"]["title"]: i for i in items}
    assert by_title["Недатированная критичная"]["bucket"] == "urgent"
    assert by_title["Недатированная критичная"]["project_name"] == "Критичный"
    assert by_title["Начатая"]["bucket"] == "started"
    assert by_title["Начатая"]["project_name"] == "Вработе"


def test_attention_next_task_per_project(conn, _seed):
    att = summary.get_attention(conn, today=TODAY)
    # у каждого проекта не больше одной следующей задачи
    projects_seen = [i["project_id"] for i in att["items"]]
    assert len(projects_seen) == len(set(projects_seen))


def test_closed_overdue_excluded_from_attention(conn, _seed):
    att = summary.get_attention(conn, today=TODAY)
    overdue_names = [p["name"] for p in att["overdue"]]
    assert overdue_names == ["ПросрочОткрытый"]  # закрытый просроченный не в списке
    assert all(i["project_id"] != _seed["c"]["id"] for i in att["items"])


def test_overdue_open_without_task_has_no_next(conn, _seed):
    att = summary.get_attention(conn, today=TODAY)
    e = next(p for p in att["overdue"] if p["name"] == "ПросрочОткрытый")
    assert e["next_task"] is None
    assert e["deadline_state"] == "overdue"


def test_no_task_projects_listed_for_cta(conn, _seed):
    att = summary.get_attention(conn, today=TODAY)
    no_task_names = {p["name"] for p in att["no_tasks"]}
    assert {"Пустой", "ПросрочОткрытый"} <= no_task_names
    assert "Критичный" not in no_task_names  # есть незакрытая задача
    assert "ЗакрытПросроч" not in no_task_names  # закрытый не зовётся к работе


def test_undated_tasks_never_disappear(conn):
    p = _project(conn, "БезДат")
    _task(conn, p["id"], "Недатированная")
    att = summary.get_attention(conn, today=TODAY)
    titles = [i["task"]["title"] for i in att["items"]]
    assert "Недатированная" in titles


def test_summary_page_attention_and_chips(client):
    over_open = client.post(
        "/api/v1/projects",
        json={"name": "ОткрытыйПросроч", "status": "active", "deadline": "2020-01-01"},
    ).json()
    closed_over = client.post(
        "/api/v1/projects",
        json={"name": "ЗакрытыйПросроч", "status": "closed", "deadline": "2020-01-01"},
    ).json()
    client.post(f"/api/v1/projects/{closed_over['id']}/tasks", json={"title": "Таск закрытого"})
    empty = client.post("/api/v1/projects", json={"name": "СовсемПустой", "status": "active"}).json()
    with_task = client.post("/api/v1/projects", json={"name": "СТаском", "status": "active"}).json()
    client.post(f"/api/v1/projects/{with_task['id']}/tasks", json={"title": "Живая задача"})

    html = client.get("/").text
    assert "Требует внимания" in html
    m = re.search(r'<section class="attention.*?</section>', html, re.S)
    assert m, "секция внимания должна быть на первом экране"
    section = m.group(0)
    assert "ОткрытыйПросроч" in section  # просроченный открытый — внимание
    assert "ЗакрытыйПросроч" not in section  # закрытый просроченный не подаётся как работа
    assert "Живая задача" in section
    # явный CTA для проекта без незакрытых задач
    assert f'href="/projects/{empty["id"]}' in section
    assert "+ Добавить задачу" in section
    # просрочка считается только по открытым
    assert "Просрочены дедлайны проектов: 1" in html
