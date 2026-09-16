"""Тесты платежей: миграция 003, CRUD, REST, UI-блок «Оплата», сводка, MCP."""

import pytest

from bort import errors
from bort.mcp import tools as mcp
from bort.services import payments, projects


@pytest.fixture()
def project(conn):
    return projects.create_project(
        conn, {"name": "С оплатой", "status": "active", "deal_amount_minor": 300_000_00}
    )


def test_migration_003_applied(conn):
    versions = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    assert "003_payments" in versions
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "payments" in tables


# --- Сервис: CRUD и суммы ---


def test_add_and_list_payments(conn, project):
    payments.add_payment(conn, project["id"], {"amount_minor": 100_000_00, "paid_on": "2026-09-01", "kind": "prepayment"})
    payments.add_payment(conn, project["id"], {"amount_minor": 50_000_00, "paid_on": "2026-09-05", "kind": "partial", "comment": "аванс"})

    listing = payments.list_payments(conn, project["id"])
    assert listing["total_minor"] == 150_000_00
    assert len(listing["items"]) == 2
    kinds = {p["kind"] for p in listing["items"]}
    assert kinds == {"prepayment", "partial"}


def test_add_payment_validates(conn, project):
    with pytest.raises(errors.ValidationError):
        payments.add_payment(conn, project["id"], {"amount_minor": 0, "paid_on": "2026-09-01"})
    with pytest.raises(errors.ValidationError):
        payments.add_payment(conn, project["id"], {"amount_minor": 100, "paid_on": "01.09.2026"})
    with pytest.raises(errors.ValidationError):
        payments.add_payment(conn, project["id"], {"amount_minor": 100, "paid_on": "2026-09-01", "kind": "deposit"})
    with pytest.raises(errors.ValidationError):
        payments.add_payment(conn, project["id"], {"amount_minor": 100, "paid_on": "2026-09-01", "currency": "RUB"})
    with pytest.raises(errors.NotFound):
        payments.add_payment(conn, 999, {"amount_minor": 100, "paid_on": "2026-09-01"})


def test_delete_payment(conn, project):
    p = payments.add_payment(conn, project["id"], {"amount_minor": 10_000_00, "paid_on": "2026-09-01"})
    payments.delete_payment(conn, p["id"])
    with pytest.raises(errors.NotFound):
        payments.get_payment(conn, p["id"])
    assert payments.list_payments(conn, project["id"])["total_minor"] == 0


def test_payments_cascade_with_project(conn, project):
    payments.add_payment(conn, project["id"], {"amount_minor": 10_000_00, "paid_on": "2026-09-01"})
    projects.delete_project(conn, project["id"])
    count = conn.execute("SELECT COUNT(*) AS c FROM payments").fetchone()["c"]
    assert count == 0


# --- REST API ---


def test_payments_api(client):
    r = client.post("/api/v1/projects", json={"name": "API-оплата", "deal_amount": "300000"})
    pid = r.json()["id"]

    r = client.post(f"/api/v1/projects/{pid}/payments", json={"amount": "100 000", "paid_on": "2026-09-01", "kind": "prepayment"})
    assert r.status_code == 201
    body = r.json()
    assert body["amount_minor"] == 10_000_000  # 100 000 ₽ = 10 000 000 коп.
    assert body["amount"] == "100000.00"

    r = client.get(f"/api/v1/projects/{pid}/payments")
    assert r.status_code == 200
    assert r.json()["total_minor"] == 10_000_000

    r = client.post(f"/api/v1/projects/{pid}/payments", json={"amount_minor": 50, "paid_on": "2026-09-02", "kind": "final"})
    payment_id = r.json()["id"]

    r = client.delete(f"/api/v1/payments/{payment_id}")
    assert r.status_code == 204
    r = client.get(f"/api/v1/projects/{pid}/payments")
    assert len(r.json()["items"]) == 1

    r = client.post(f"/api/v1/projects/{pid}/payments", json={"amount_minor": 0, "paid_on": "2026-09-01"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation"

    r = client.get("/api/v1/projects/999/payments")
    assert r.status_code == 404


# --- Сводка: оплачено в строках и в проекте ---


def test_summary_includes_paid(conn, project):
    from bort.services import summary

    payments.add_payment(conn, project["id"], {"amount_minor": 120_000_00, "paid_on": "2026-09-01", "kind": "prepayment"})
    result = summary.get_summary(conn, today=__import__("datetime").date(2026, 9, 9))
    row = result["projects"][0]
    assert row["paid_minor"] == 120_000_00

    s = summary.get_project_summary(conn, project["id"])
    assert s["payments_total_minor"] == 120_000_00
    assert s["remaining_minor"] == 180_000_00
    assert s["payment_progress"] == 40


def test_summary_tasks_filter(conn):
    from bort.services import summary, tasks

    full = projects.create_project(conn, {"name": "Все закрыты", "status": "active"})
    open_p = projects.create_project(conn, {"name": "Есть открытые", "status": "active"})
    no_tasks = projects.create_project(conn, {"name": "Без задач", "status": "active"})

    t1 = tasks.create_task(conn, full["id"], {"title": "A"})
    t2 = tasks.create_task(conn, full["id"], {"title": "B"})
    tasks.close_task(conn, t1["id"], "done")
    tasks.close_task(conn, t2["id"], "done")
    tasks.create_task(conn, open_p["id"], {"title": "C"})

    TODAY = __import__("datetime").date(2026, 9, 9)
    names_closed = [p["name"] for p in summary.get_summary(conn, scope="all", today=TODAY, tasks="closed")["projects"]]
    assert names_closed == ["Все закрыты"]

    names_open = [p["name"] for p in summary.get_summary(conn, scope="all", today=TODAY, tasks="open")["projects"]]
    assert names_open == ["Есть открытые"]

    names_any = [p["name"] for p in summary.get_summary(conn, scope="all", today=TODAY)["projects"]]
    assert len(names_any) == 3

    with pytest.raises(errors.ValidationError):
        summary.get_summary(conn, tasks="weird")


# --- UI: карточка проекта, блок «Оплата» ---


def test_project_page_contains_payments_block(client):
    r = client.post("/api/v1/projects", json={"name": "UI-оплата", "deal_amount": "300000"})
    pid = r.json()["id"]
    client.post(f"/api/v1/projects/{pid}/payments", json={"amount": "100 000", "paid_on": "2026-09-01", "kind": "prepayment"})

    r = client.get(f"/projects/{pid}")
    assert r.status_code == 200
    html = r.text
    assert "Оплата" in html
    assert "Оплачено" in html
    assert "Предоплата" in html
    assert "100 000,00" in html
    assert "Остаток к оплате" in html  # раньше «Осталось» в дублирующей карточке

    # htmx-создание платежа
    r = client.post(
        f"/ui/projects/{pid}/payments",
        data={"amount": "50 000,50", "kind": "partial", "paid_on": "2026-09-05", "comment": "второй транш"},
    )
    assert r.status_code == 200
    assert "50 000,50" in r.text

    # Ошибка валидации отображается в блоке
    r = client.post(f"/ui/projects/{pid}/payments", data={"amount": "abc", "kind": "partial", "paid_on": "2026-09-05"})
    assert "form-error" in r.text


def test_summary_page_has_paid_column_and_tasks_filter(client):
    client.post("/api/v1/projects", json={"name": "Колонка", "deal_amount": "100000"})
    r = client.get("/")
    assert r.status_code == 200
    assert "Оплачено" in r.text
    # селекта фильтра и поиска на экране больше нет — фильтры живут в URL
    assert "Задачи: любые" not in r.text
    assert 'id="filters"' not in r.text
    assert client.get("/", params={"tasks": "open"}).status_code == 200

    # Фильтр задач через UI-эндпоинт
    r = client.get("/ui/projects", params={"scope": "all", "tasks": "closed"})
    assert r.status_code == 200
    r = client.get("/api/v1/summary", params={"tasks": "closed"})
    assert r.status_code == 200
    r = client.get("/api/v1/summary", params={"tasks": "bad"})
    assert r.status_code == 422


# --- Канбан на карточке проекта: вид по умолчанию + колонки ---


def test_kanban_lazy_view(client):
    r = client.post("/api/v1/projects", json={"name": "Канбан-проект"})
    pid = r.json()["id"]
    client.post(f"/api/v1/projects/{pid}/tasks", json={"title": "Дизайн", "priority": 1, "deadline": "2026-09-01"})
    client.post(f"/api/v1/projects/{pid}/tasks", json={"title": "Вёрстка"})

    # Канбан — вид по умолчанию: карточки отрисованы сразу
    page = client.get(f"/projects/{pid}").text
    assert 'id="kanban-block"' in page
    assert "kanban-card" in page
    assert "Дизайн" in page
    assert 'class="subtab active" data-subtab="kanban"' in page

    # GET с view=kanban отдаёт 4 колонки и карточки задач
    r = client.get(f"/ui/projects/{pid}/tasks", params={"view": "kanban"})
    assert r.status_code == 200
    html = r.text
    for head in ("К выполнению", "В работе", "На проверке", "Готово"):
        assert head in html
    assert "Дизайн" in html and "Вёрстка" in html
    assert "kanban-card" in html
    # Просроченный дедлайн — красный чип
    assert "d-overdue" in html
    # Смена статуса одной кнопкой
    assert 'hx-vals=' in html and '"view": "kanban"' in html

    # Поиск фильтрует и канбан
    r = client.get(f"/ui/projects/{pid}/tasks", params={"view": "kanban", "q": "дизайн"})
    assert "Дизайн" in r.text
    assert "Вёрстка" not in r.text

    # Смена статуса из канбана возвращает обновлённый канбан
    tasks = client.get(f"/api/v1/projects/{pid}/tasks").json()["items"]
    design = next(t for t in tasks if t["title"] == "Дизайн")
    r = client.post(f"/ui/tasks/{design['id']}/status", data={"status": "in_progress", "view": "kanban"})
    assert r.status_code == 200
    assert 'id="kanban-block"' in r.text
    assert "В работе" in r.text


# --- MCP: payments_total_minor / payment_progress ---


def test_mcp_project_summary_and_get_include_payments(mcpcall):
    r = mcpcall(mcp._project_create, name="MCP-оплата", deal_amount_minor=300_000_00)
    pid = r["project"]["id"]
    mcpcall(mcp._expense_add, project_id=pid, amount_minor=10_000)

    # Платёж через сервис (создание платежа — не MCP-инструмент по спецификации)
    db_path = __import__("os").environ["BORT_DB"]
    from bort import db as db_mod
    from bort.services import payments as payments_svc

    conn = db_mod.connect(db_path)
    try:
        payments_svc.add_payment(conn, pid, {"amount_minor": 150_000_00, "paid_on": "2026-09-01", "kind": "prepayment"})
    finally:
        conn.close()

    s = mcpcall(mcp._project_summary, name="MCP-оплата")
    assert s["ok"] is True
    assert s["summary"]["payments_total_minor"] == 150_000_00
    assert s["summary"]["payment_progress"] == 50
    assert s["summary"]["remaining_minor"] == 150_000_00

    pg = mcpcall(mcp._project_get, project_id=pid)
    assert pg["ok"] is True
    assert pg["payments_total_minor"] == 150_000_00
    assert pg["payment_progress"] == 50
    assert pg["payments_total"] == "150000.00"
    assert len(pg["payments"]) == 1
