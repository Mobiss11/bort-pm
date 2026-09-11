"""Демо-данные для проверки UI: 6 проектов с разными дедлайнами, задачами и затратами.

Использование:
    uv run python scripts/seed_demo.py            # налить (если база пуста)
    uv run python scripts/seed_demo.py --wipe     # убрать всё демо/рабочее: проекты, задачи,
                                                  # затраты, чаты и люди (справочники тоже чистятся)
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bort.dates import today_local
from bort.db import connect
from bort.services import expenses, projects, tasks


def _wipe(conn) -> None:
    # Порядок не критичен: FK-каскады сносят задачи/затраты вместе с проектами
    for stmt in (
        "DELETE FROM projects",
        "DELETE FROM chats",
        "DELETE FROM people",
    ):
        conn.execute(stmt)
    conn.commit()
    print("База очищена: проекты (вместе с задачами и затратами), чаты, люди.")


def main() -> None:
    conn = connect()
    if "--wipe" in sys.argv:
        _wipe(conn)
        return

    if projects.list_projects(conn, limit=1)["items"]:
        print("В базе уже есть проекты — сид пропущен (очистить: scripts/seed_demo.py --wipe).")
        return

    today = today_local()

    def d(offset: int) -> str:
        return (today + timedelta(days=offset)).isoformat()

    data = [
        {"name": "Демо: Просроченный", "status": "active", "priority": 1,
         "deal_amount_minor": 150_000_00, "deadline": d(-4),
         "notes": "Дедлайн уже прошёл — красная строка на сводке."},
        {"name": "Демо: Горящий", "status": "active", "priority": 1,
         "deal_amount_minor": 90_000_00, "deadline": d(1),
         "notes": "Дедлайн завтра/сегодня — оранжевая строка."},
        {"name": "Демо: Скоро", "status": "active", "priority": 2,
         "deal_amount_minor": 250_000_00, "deadline": d(11)},
        {"name": "Демо: Обычный", "status": "active", "priority": 3,
         "deal_amount_minor": 65_000_00, "deadline": d(53)},
        {"name": "Демо: Без задач", "status": "active", "priority": 4,
         "deal_amount_minor": 40_000_00,
         "notes": "Проект без задач — прогресс «—», это норма."},
        {"name": "Демо: Закрыт", "status": "closed", "priority": 3,
         "deal_amount_minor": 30_000_00, "finished_on": d(-25)},
    ]
    ids = [projects.create_project(conn, project)["id"] for project in data]

    tasks.create_task(conn, ids[0], {"title": "Согласовать правки", "priority": 1, "deadline": d(-5)})
    tasks.create_task(conn, ids[0], {"title": "Отправить счёт", "priority": 2})
    tasks.create_task(conn, ids[1], {"title": "Финальная сборка", "priority": 1, "deadline": d(0)})
    tasks.create_task(conn, ids[1], {"title": "Тесты", "status": "in_progress"})
    tasks.create_task(conn, ids[2], {"title": "Созвон с заказчиком", "status": "done"})
    tasks.create_task(conn, ids[3], {"title": "Черновик ТЗ", "status": "review"})

    expenses.add_expense(conn, ids[0], {"amount_minor": 45_000_00, "spent_on": d(-20), "category_code": "contractors", "comment": "Подрядчик за модуль"})
    expenses.add_expense(conn, ids[0], {"amount_minor": 2_500_00, "spent_on": d(-12), "category_code": "subscriptions", "comment": "Хостинг"})
    expenses.add_expense(conn, ids[1], {"amount_minor": 30_000_00, "spent_on": d(-8), "category_code": "ads", "comment": "Реклама запуска"})
    expenses.add_expense(conn, ids[2], {"amount_minor": 12_000_00, "spent_on": d(-6), "category_code": "other"})
    expenses.add_expense(conn, ids[3], {"amount_minor": 500_00, "spent_on": d(-1), "category_code": "subscriptions"})

    print(f"Демо-данные налиты: {len(data)} проектов, задачи, затраты.")


if __name__ == "__main__":
    main()
