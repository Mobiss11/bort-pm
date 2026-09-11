"""Тесты сервисного слоя задач: CRUD, close, closed_at-семантика."""

import pytest

from bort import errors
from bort.services import projects, tasks


@pytest.fixture()
def project(conn):
    return projects.create_project(conn, {"name": "Проект с задачами", "status": "active"})


def test_create_task_defaults(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "Сделать"})
    assert t["project_id"] == project["id"]
    assert t["status"] == "todo"
    assert t["priority"] == 3
    assert t["position"] == 0
    assert t["deadline"] is None
    assert t["closed_at"] is None


def test_create_task_missing_project(conn):
    with pytest.raises(errors.NotFound):
        tasks.create_task(conn, 999, {"title": "Сирота"})


@pytest.mark.parametrize(
    "bad",
    [
        {"title": "  "},
        {"title": "X", "status": "archived"},
        {"title": "X", "priority": 9},
        {"title": "X", "deadline": "завтра"},
        {"title": "X", "whatever": 1},
    ],
)
def test_create_task_rejects_invalid(conn, project, bad):
    with pytest.raises(errors.ValidationError):
        tasks.create_task(conn, project["id"], bad)


def test_update_task_fields(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "Задача"})
    updated = tasks.update_task(
        conn, t["id"], {"status": "in_progress", "priority": 1, "deadline": "2026-09-15"}
    )
    assert updated["status"] == "in_progress"
    assert updated["priority"] == 1
    assert updated["deadline"] == "2026-09-15"
    assert updated["closed_at"] is None


def test_close_done_sets_closed_at(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "Закрыть"})
    closed = tasks.close_task(conn, t["id"], "done")
    assert closed["status"] == "done"
    assert closed["closed_at"] is not None
    assert closed["closed_at"].endswith("Z")


def test_close_cancelled(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "Отменить"})
    closed = tasks.close_task(conn, t["id"], "cancelled")
    assert closed["status"] == "cancelled"
    assert closed["closed_at"] is not None


def test_close_default_outcome_is_done(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "По умолчанию"})
    assert tasks.close_task(conn, t["id"])["status"] == "done"


def test_close_rejects_bad_outcome(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "X"})
    with pytest.raises(errors.ValidationError):
        tasks.close_task(conn, t["id"], "postponed")


def test_reopen_clears_closed_at(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "Переоткрыть"})
    tasks.close_task(conn, t["id"], "done")
    reopened = tasks.update_task(conn, t["id"], {"status": "review"})
    assert reopened["status"] == "review"
    assert reopened["closed_at"] is None


def test_list_tasks_status_filter_and_order(conn, project):
    t1 = tasks.create_task(conn, project["id"], {"title": "A", "priority": 3})
    t2 = tasks.create_task(conn, project["id"], {"title": "B", "priority": 1})
    t3 = tasks.create_task(conn, project["id"], {"title": "C", "priority": 2})
    tasks.close_task(conn, t2["id"], "done")

    all_tasks = tasks.list_tasks(conn, project["id"])
    # Порядок списка: position ASC, priority ASC, id ASC → B(1), C(2), A(3)
    assert [t["id"] for t in all_tasks] == [t2["id"], t3["id"], t1["id"]]

    open_tasks = tasks.list_tasks(conn, project["id"], status="todo")
    assert {t["id"] for t in open_tasks} == {t1["id"], t3["id"]}

    done_tasks = tasks.list_tasks(conn, project["id"], status="done")
    assert [t["id"] for t in done_tasks] == [t2["id"]]


def test_delete_task(conn, project):
    t = tasks.create_task(conn, project["id"], {"title": "Удалить"})
    tasks.delete_task(conn, t["id"])
    with pytest.raises(errors.NotFound):
        tasks.get_task(conn, t["id"])


def test_missing_task_raises_not_found(conn, project):
    with pytest.raises(errors.NotFound):
        tasks.get_task(conn, 999)


# --- Глобальный список задач по всем проектам ---


def test_list_all_tasks_filters(conn):
    p1 = projects.create_project(conn, {"name": "Проект А"})
    p2 = projects.create_project(conn, {"name": "Проект Б"})
    tasks.create_task(conn, p1["id"], {"title": "Дизайн", "priority": 1})
    tasks.create_task(conn, p1["id"], {"title": "Код"})
    t3 = tasks.create_task(conn, p2["id"], {"title": "Отчёт"})
    tasks.close_task(conn, t3["id"], "done")

    all_tasks = tasks.list_all_tasks(conn)
    assert len(all_tasks) == 3
    assert {t["project_name"] for t in all_tasks} == {"Проект А", "Проект Б"}

    by_project = tasks.list_all_tasks(conn, project_id=p1["id"])
    assert [t["title"] for t in by_project] == ["Дизайн", "Код"]  # приоритет 1 выше

    assert [t["title"] for t in tasks.list_all_tasks(conn, status="done")] == ["Отчёт"]
    assert [t["title"] for t in tasks.list_all_tasks(conn, q="диз")] == ["Дизайн"]  # кириллица, регистр
    assert tasks.list_all_tasks(conn, q="несуществующая") == []


# --- Страница /tasks: глобальный канбан ---


def test_global_tasks_page(client):
    p = client.post("/api/v1/projects", json={"name": "Глобальный"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "Задача глобальная", "priority": 1})

    r = client.get("/tasks")
    assert r.status_code == 200
    for head in ("К выполнению", "В работе", "На проверке", "Готово"):
        assert head in r.text
    assert "Задача глобальная" in r.text
    assert "Глобальный" in r.text
    assert 'href="/tasks"' in r.text          # пункт в сайдбаре
    assert "kanban-count" in r.text           # счётчики колонок
    assert "Проект: все" in r.text            # фильтр по проекту


def test_global_board_filters_and_status_change(client):
    p1 = client.post("/api/v1/projects", json={"name": "Первый"}).json()
    p2 = client.post("/api/v1/projects", json={"name": "Второй"}).json()
    t1 = client.post(f"/api/v1/projects/{p1['id']}/tasks", json={"title": "Задача один"}).json()
    client.post(f"/api/v1/projects/{p2['id']}/tasks", json={"title": "Задача два"})

    # Фильтр по проекту
    r = client.get("/ui/tasks/board", params={"project_id": p2["id"]})
    assert "Задача два" in r.text and "Задача один" not in r.text

    # Поиск
    r = client.get("/ui/tasks/board", params={"q": "один"})
    assert "Задача один" in r.text and "Задача два" not in r.text

    # Смена статуса из глобального канбана возвращает глобальный канбан
    r = client.post(
        f"/ui/tasks/{t1['id']}/status",
        data={"status": "in_progress", "view": "global"},
    )
    assert r.status_code == 200
    assert 'id="global-kanban"' in r.text
    body = client.get("/api/v1/tasks/1").json()
    assert body["status"] == "in_progress"


# --- Карточка проекта: канбан — вид по умолчанию ---


def test_project_card_kanban_default(client):
    p = client.post("/api/v1/projects", json={"name": "Канбан по умолчанию"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "Видимая в канбане"})

    page = client.get(f"/projects/{p['id']}").text
    assert 'class="subtab active" data-subtab="kanban"' in page
    assert 'data-subpanel="list" hidden' in page
    assert "kanban-card" in page                      # канбан отрисован сразу
    assert "Видимая в канбане" in page
    assert "Канбан загрузится" not in page            # ленивая загрузка больше не нужна
