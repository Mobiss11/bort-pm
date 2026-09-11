"""Тесты сервисного слоя проектов, включая явную проверку каскадного удаления."""

import pytest

from bort import errors
from bort.services import expenses, projects, tasks


def test_create_minimal_defaults(conn):
    p = projects.create_project(conn, {"name": "Сайт"})
    assert p["id"] > 0
    assert p["name"] == "Сайт"
    assert p["status"] == "idea"
    assert p["priority"] == 2
    assert p["deal_amount_minor"] == 0
    assert p["currency"] == "RUB"
    assert p["deadline"] is None
    assert p["created_at"].endswith("Z")


def test_create_full(conn):
    p = projects.create_project(
        conn,
        {
            "name": "Мобильное приложение",
            "status": "active",
            "priority": 1,
            "deal_amount_minor": 1_500_000_00,
            "currency": "RUB",
            "deadline": "2026-10-01",
            "started_on": "2026-09-01",
            "notes": "важно",
        },
    )
    assert p["status"] == "active"
    assert p["priority"] == 1
    assert p["deal_amount_minor"] == 1_500_000_00
    assert p["deadline"] == "2026-10-01"
    assert p["started_on"] == "2026-09-01"


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "   "},
        {"name": "X", "status": "running"},
        {"name": "X", "priority": 5},
        {"name": "X", "priority": 0},
        {"name": "X", "deal_amount_minor": -1},
        {"name": "X", "currency": "RUBS"},
        {"name": "X", "deadline": "01.10.2026"},
        {"name": "X", "deadline": "2026-13-01"},
        {"name": "X", "unexpected_field": 1},
    ],
)
def test_create_rejects_invalid(conn, bad):
    with pytest.raises(errors.ValidationError):
        projects.create_project(conn, bad)


def test_get_missing_raises_not_found(conn):
    with pytest.raises(errors.NotFound):
        projects.get_project(conn, 999)


def test_update_partial(conn):
    p = projects.create_project(conn, {"name": "Проект", "priority": 2})
    updated = projects.update_project(
        conn, p["id"], {"priority": 1, "deadline": "2026-09-20"}
    )
    assert updated["priority"] == 1
    assert updated["deadline"] == "2026-09-20"
    assert updated["name"] == "Проект"
    assert updated["status"] == "idea"


def test_update_clears_nullable_with_explicit_none(conn):
    p = projects.create_project(conn, {"name": "Проект", "deadline": "2026-09-20"})
    updated = projects.update_project(conn, p["id"], {"deadline": None})
    assert updated["deadline"] is None


def test_update_updated_at_bumped_by_trigger(conn):
    import time

    p = projects.create_project(conn, {"name": "Проект"})
    time.sleep(1.1)
    updated = projects.update_project(conn, p["id"], {"notes": "новая заметка"})
    assert updated["updated_at"] > p["updated_at"]


def test_update_no_fields_returns_current(conn):
    p = projects.create_project(conn, {"name": "Проект"})
    same = projects.update_project(conn, p["id"], {})
    assert same == p


def test_update_missing_raises_not_found(conn):
    with pytest.raises(errors.NotFound):
        projects.update_project(conn, 999, {"name": "Нет"})


def test_list_filters_status_priority_and_q(conn):
    a = projects.create_project(conn, {"name": "Проект Альфа", "status": "active", "priority": 1})
    projects.create_project(conn, {"name": "Проект Бета", "status": "idea", "priority": 2})
    c = projects.create_project(conn, {"name": "Гамма", "status": "active", "priority": 4})

    active = projects.list_projects(conn, status="active")
    assert {p["id"] for p in active["items"]} == {a["id"], c["id"]}
    assert active["total"] == 2

    prio = projects.list_projects(conn, priority=2)
    assert prio["total"] == 1

    # Кириллический поиск без учёта регистра
    found = projects.list_projects(conn, q="альфа")
    assert found["total"] == 1
    assert found["items"][0]["id"] == a["id"]

    paged = projects.list_projects(conn, limit=1, offset=1)
    assert len(paged["items"]) == 1
    assert paged["total"] == 3

    with pytest.raises(errors.ValidationError):
        projects.list_projects(conn, status="unknown")
    with pytest.raises(errors.ValidationError):
        projects.list_projects(conn, sort="magic")


def test_list_default_sort_priority_then_name(conn):
    p3 = projects.create_project(conn, {"name": "зета", "priority": 3})
    p1 = projects.create_project(conn, {"name": "бета", "priority": 1})
    p2 = projects.create_project(conn, {"name": "Альфа", "priority": 2})
    names = [p["name"] for p in projects.list_projects(conn)["items"]]
    assert names == ["бета", "Альфа", "зета"]
    ids = [p["id"] for p in projects.list_projects(conn)["items"]]
    assert ids == [p1["id"], p2["id"], p3["id"]]


def test_delete_project_cascades_tasks_and_expenses(conn):
    """Явная проверка каскада: удалили проект — задачи и затраты исчезли."""
    p = projects.create_project(conn, {"name": "С каскадом"})
    t1 = tasks.create_task(conn, p["id"], {"title": "Задача 1"})
    tasks.create_task(conn, p["id"], {"title": "Задача 2"})
    expenses.add_expense(
        conn, p["id"], {"amount_minor": 5_000_00, "spent_on": "2026-09-01", "category_code": "other"}
    )
    expenses.add_expense(
        conn,
        p["id"],
        {"amount_minor": 700, "spent_on": "2026-09-02", "category_code": "ads"},
    )

    assert len(tasks.list_tasks(conn, p["id"])) == 2
    assert expenses.list_expenses(conn, p["id"])["total_minor"] == 5_000_00 + 700

    projects.delete_project(conn, p["id"])

    with pytest.raises(errors.NotFound):
        projects.get_project(conn, p["id"])
    with pytest.raises(errors.NotFound):
        tasks.get_task(conn, t1["id"])

    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM tasks) AS t, (SELECT COUNT(*) FROM expenses) AS e"
    ).fetchone()
    assert counts["t"] == 0
    assert counts["e"] == 0


def test_delete_missing_raises_not_found(conn):
    with pytest.raises(errors.NotFound):
        projects.delete_project(conn, 999)
