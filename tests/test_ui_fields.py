"""Тесты новых UI-эндпоинтов: инлайн-правка приоритета/дедлайна, удаление затрат,
создание задачи из глобального канбана, парсинг сумм с экзотическими пробелами."""

import pytest

from bort.money import to_minor


def _mk_project(client, name="П1", **extra):
    payload = {"name": name, "status": "active", "priority": 2, "deal_amount": "100 000"}
    payload.update(extra)
    r = client.post("/api/v1/projects", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _mk_task(client, project_id, title="Задача"):
    r = client.post(f"/api/v1/projects/{project_id}/tasks", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()


def test_to_minor_exotic_spaces():
    assert to_minor("25 000") == 2_500_000
    assert to_minor("25\u00a0000") == 2_500_000      # NBSP
    assert to_minor("25\u202f000") == 2_500_000      # NNBSP
    assert to_minor("25\u2009000") == 2_500_000      # thin
    assert to_minor("25\u200a000") == 2_500_000      # hair
    assert to_minor("25\u2007000") == 2_500_000      # figure
    assert to_minor("25\u3000000") == 2_500_000      # ideographic
    assert to_minor("150 000,50") == 15_000_050
    assert to_minor("25000") == 2_500_000
    with pytest.raises(ValueError):
        to_minor("abc")


def test_ui_fields_update_priority_and_deadline(client):
    p = _mk_project(client)
    pid = p["id"]
    r = client.post(f"/ui/projects/{pid}/fields", data={"priority": "1"})
    assert r.status_code == 200
    assert 'value="1" selected' in r.text and "Критичный" in r.text

    r = client.post(f"/ui/projects/{pid}/fields", data={"deadline": "2026-12-31"})
    assert r.status_code == 200
    assert "31.12.2026" in r.text

    # пустой deadline = снять
    r = client.post(f"/ui/projects/{pid}/fields", data={"deadline": ""})
    assert r.status_code == 200
    # проверить фактическое состояние через API
    r = client.get(f"/api/v1/projects/{pid}")
    assert r.json()["priority"] == 1
    assert r.json()["deadline"] is None


def test_ui_fields_card_view(client):
    p = _mk_project(client, name="Карточка")
    r = client.post(f"/ui/projects/{p['id']}/fields", data={"deadline": "2026-10-01", "view": "card"})
    assert r.status_code == 200
    assert "01.10.2026" in r.text
    assert "Дедлайн: 01.10.2026" in r.text
    assert 'id="project-head-fields"' in r.text


def test_ui_delete_expense(client):
    p = _mk_project(client, name="Затратный")
    pid = p["id"]
    r = client.post(
        f"/api/v1/projects/{pid}/expenses",
        json={"amount": "5 000", "spent_on": "2026-09-10", "category_code": "other"},
    )
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    r = client.delete(f"/ui/projects/{pid}/expenses/{eid}")
    assert r.status_code == 200
    assert "Затрат пока нет" in r.text
    r = client.get(f"/api/v1/projects/{pid}")
    assert r.json()["expenses"]["total_minor"] == 0


def test_ui_create_task_global(client):
    p = _mk_project(client, name="Канбан")
    r = client.post(
        "/ui/tasks",
        data={"project_id": str(p["id"]), "title": "Новая из канбана", "priority": "2"},
    )
    assert r.status_code == 200
    assert "Новая из канбана" in r.text
    # без проекта — ошибка формы, но 200
    r = client.post("/ui/tasks", data={"title": "Без проекта"})
    assert r.status_code == 200
    assert "Выберите проект" in r.text


def test_ui_task_status_drag_endpoint(client):
    """Эндпоинт, на который ложится drag-and-drop: смена статуса и возврат доски."""
    p = _mk_project(client, name="Драг")
    t = _mk_task(client, p["id"], "Карточка")
    r = client.post(f"/ui/tasks/{t['id']}/status", data={"status": "in_progress", "view": "global"})
    assert r.status_code == 200
    assert 'id="global-kanban"' in r.text
    r = client.get(f"/api/v1/projects/{p['id']}/tasks")
    assert r.json()["items"][0]["status"] == "in_progress"