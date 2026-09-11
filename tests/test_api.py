"""Тесты REST API: TestClient, полный проход и единый конверт ошибок."""

# Фикстура client — в conftest.py


# --- Полный проход: проект → задача → затрата → сводка → удаление ---


def test_full_pass(client):
    # 1. Проект: сумма пришла человеком, уходит в двух видах
    r = client.post(
        "/api/v1/projects",
        json={"name": "Сайт", "deal_amount": "150 000,50", "deadline": "2026-09-20"},
    )
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["deal_amount_minor"] == 15_000_050
    assert project["deal_amount"] == "150000.50"
    pid = project["id"]

    # 2. Задача + закрытие
    r = client.post(f"/api/v1/projects/{pid}/tasks", json={"title": "Сверстать главную"})
    assert r.status_code == 201
    task = r.json()
    assert task["status"] == "todo"
    r = client.post(f"/api/v1/tasks/{task['id']}/close", json={"status": "done"})
    assert r.status_code == 200
    assert r.json()["closed_at"] is not None

    # 3. Затрата: сумма человеком с пробелами и запятой
    r = client.post(
        f"/api/v1/projects/{pid}/expenses",
        json={"amount": "25 000,50", "spent_on": "2026-09-01", "category_code": "contractors"},
    )
    assert r.status_code == 201
    expense = r.json()
    assert expense["amount_minor"] == 2_500_050
    assert expense["amount"] == "25000.50"

    # Затрата в чужой валюте → 422 конверт
    r = client.post(
        f"/api/v1/projects/{pid}/expenses",
        json={"amount_minor": 100, "spent_on": "2026-09-01", "category_code": "other", "currency": "USD"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation"

    # 4. Сводка совпадает с сервисным слоем
    r = client.get("/api/v1/summary")
    assert r.status_code == 200
    summary = r.json()
    assert summary["totals"]["projects_count"] == 1
    assert summary["totals"]["deal_total_minor"] == 15_000_050
    assert summary["totals"]["expenses_total_minor"] == 2_500_050
    assert summary["totals"]["margin_total_minor"] == 12_500_000
    assert summary["totals"]["deal_total"] == "150000.50"
    row = summary["projects"][0]
    assert row["tasks_done"] == 1
    assert row["deadline_state"] in {"hot", "soon", "normal", "overdue", "none"}

    # Сводка одного проекта с разбивкой по категориям
    r = client.get(f"/api/v1/projects/{pid}/summary")
    assert r.status_code == 200
    ps = r.json()
    assert ps["expenses_by_category"] == [
        {"category_code": "contractors", "total_minor": 2_500_050, "total": "25000.50", "count": 1}
    ]

    # 5. Обновление человекочитаемой суммы через PATCH
    r = client.patch(f"/api/v1/projects/{pid}", json={"deal_amount": "200000"})
    assert r.status_code == 200
    assert r.json()["deal_amount_minor"] == 20_000_000

    # 6. Каскадное удаление
    r = client.delete(f"/api/v1/projects/{pid}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/projects/{pid}").status_code == 404
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    # Затрата унесена каскадом: DELETE по несуществующей → 404 (GET /expenses/{id} в §4 нет)
    r = client.delete(f"/api/v1/expenses/{expense['id']}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    r = client.get("/api/v1/summary")
    assert r.json()["totals"]["projects_count"] == 0


# --- Проекты: списки, фильтры ---


def test_list_projects_query_and_shaping(client):
    client.post("/api/v1/projects", json={"name": "Альфа", "status": "active", "priority": 1})
    client.post("/api/v1/projects", json={"name": "Бета", "status": "idea"})

    r = client.get("/api/v1/projects", params={"status": "active"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Альфа"

    r = client.get("/api/v1/projects", params={"q": "бет"})
    assert r.json()["total"] == 1

    r = client.get("/api/v1/projects", params={"sort": "magic"})
    assert r.status_code == 422


def test_get_project_nested(client):
    r = client.post("/api/v1/projects", json={"name": "Проект"})
    pid = r.json()["id"]
    client.post(f"/api/v1/projects/{pid}/tasks", json={"title": "T"})
    client.post(
        f"/api/v1/projects/{pid}/expenses",
        json={"amount_minor": 500, "spent_on": "2026-09-01", "category_code": "ads"},
    )
    r = client.get(f"/api/v1/projects/{pid}")
    body = r.json()
    assert len(body["tasks"]) == 1
    assert body["expenses"]["total_minor"] == 500
    assert body["expenses"]["items"][0]["amount"] == "5.00"
    assert body["chats"] == [] and body["people"] == []


# --- Конверт ошибок ---


def test_error_envelope_not_found(client):
    r = client.get("/api/v1/projects/999")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert "message" in body["error"]
    assert body["error"]["details"]["project_id"] == 999


def test_error_envelope_unknown_route(client):
    r = client.get("/api/v1/definitely/not/here")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_error_envelope_validation(client):
    r = client.post("/api/v1/projects", json={"name": "   "})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation"
    assert "name" in body["error"]["details"]

    # Не-JSON-объект
    r = client.post("/api/v1/projects", json=[1, 2, 3])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation"


def test_error_envelope_bad_path_param_and_both_money_forms(client):
    r = client.get("/api/v1/projects/not-an-int")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation"

    r = client.post(
        "/api/v1/projects",
        json={"name": "X", "deal_amount_minor": 100, "deal_amount": "1.00"},
    )
    assert r.status_code == 422
    assert "обе формы" in r.json()["error"]["message"]

    r = client.post("/api/v1/projects", json={"name": "X", "deal_amount": "много"})
    assert r.status_code == 422
    assert "deal_amount" in r.json()["error"]["details"]

    r = client.post("/api/v1/tasks/1/close", json={"status": "postponed"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation"


# --- Чаты и люди ---


def test_chats_people_full_flow(client):
    # Люди
    r = client.post("/api/v1/people", json={"full_name": "Иван Иванов", "tg_username": "@ivan"})
    assert r.status_code == 201
    person = r.json()
    assert person["tg_username"] == "ivan"  # @ снят

    r = client.post("/api/v1/people", json={"full_name": "Иван Иванов", "tg_username": "@ivan"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"

    # Чат
    r = client.post(
        "/api/v1/chats",
        json={"title": "Проект Сайт", "kind": "group", "tg_chat_id": "-1001234567890"},
    )
    assert r.status_code == 201
    chat = r.json()
    assert chat["members"] == [] and chat["projects"] == []

    r = client.post("/api/v1/chats", json={"title": "Дубль", "tg_chat_id": "-1001234567890"})
    assert r.status_code == 409

    # Участник по person_id и по full_name
    r = client.post(f"/api/v1/chats/{chat['id']}/members", json={"person_id": person["id"], "role": "заказчик"})
    assert r.status_code == 201
    r = client.post(f"/api/v1/chats/{chat['id']}/members", json={"full_name": "Пётр Петров", "tg_username": "@petya"})
    assert r.status_code == 201
    r = client.post(f"/api/v1/chats/{chat['id']}/members", json={"person_id": person["id"]})
    assert r.status_code == 409

    # Привязка чата к проекту существующим chat_id
    r = client.post("/api/v1/projects", json={"name": "Проект с чатом"})
    pid = r.json()["id"]
    r = client.post(f"/api/v1/projects/{pid}/chats", json={"chat_id": chat["id"], "note": "рабочий чат"})
    assert r.status_code == 201
    assert any(p["id"] == pid for p in r.json()["projects"])

    # Привязка созданием нового чата прямо из проекта
    r = client.post(f"/api/v1/projects/{pid}/chats", json={"title": "Чат подрядчика", "kind": "private"})
    assert r.status_code == 201
    chat2_id = r.json()["id"]

    r = client.get(f"/api/v1/projects/{pid}/chats")
    assert len(r.json()["items"]) == 2

    # Справочник: один и тот же чат — одна запись
    r = client.get("/api/v1/chats", params={"q": "подрядчика"})
    assert r.json()["total"] == 1

    # Отвязка: чат переживает отвязку
    r = client.delete(f"/api/v1/projects/{pid}/chats/{chat2_id}")
    assert r.status_code == 204
    r = client.get(f"/api/v1/projects/{pid}/chats")
    assert [c["id"] for c in r.json()["items"]] == [chat["id"]]
    r = client.get(f"/api/v1/chats/{chat2_id}")
    assert r.status_code == 200
    assert r.json()["projects"] == []

    # Привязка человека к проекту по full_name
    r = client.post(f"/api/v1/projects/{pid}/people", json={"full_name": "Анна Смирнова", "role": "дизайнер"})
    assert r.status_code == 201
    anna = r.json()
    assert any(p["id"] == pid for p in anna["projects"])
    r = client.post(f"/api/v1/projects/{pid}/people", json={"person_id": anna["id"]})
    assert r.status_code == 409

    # Удаление участника из чата
    r = client.delete(f"/api/v1/chats/{chat['id']}/members/{person['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/chats/{chat['id']}").json()["members"][0]["full_name"] == "Пётр Петров"

    # Удаление проекта: чаты и люди остаются в справочнике
    r = client.delete(f"/api/v1/projects/{pid}")
    assert r.status_code == 204
    assert client.get("/api/v1/chats").json()["total"] == 2
    assert client.get("/api/v1/people").json()["total"] == 3


# --- Затраты: категории ---


def test_expense_categories(client):
    r = client.get("/api/v1/expense-categories")
    assert r.status_code == 200
    codes = [c["code"] for c in r.json()["items"]]
    assert codes == ["contractors", "subscriptions", "ads", "other"]

    r = client.post(
        "/api/v1/expense-categories", json={"code": "equipment", "title_ru": "Оборудование", "sort_order": 40}
    )
    assert r.status_code == 201
    assert r.json()["is_active"] is True or r.json()["is_active"] == 1

    r = client.post("/api/v1/expense-categories", json={"code": "ads", "title_ru": "Дубль"})
    assert r.status_code == 409

    r = client.post("/api/v1/expense-categories", json={"code": "Bad Code", "title_ru": "X"})
    assert r.status_code == 422


# --- Служебное ---


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["wal"] is True
    assert body["schema_version"] == "003_payments"
    assert body["db_path"].endswith("api-test.db")


def test_meta_enums(client):
    r = client.get("/api/v1/meta/enums")
    assert r.status_code == 200
    enums = r.json()
    assert {"project_statuses", "task_statuses", "priorities", "chat_kinds"} <= set(enums)
    by_code = {e["code"]: e["title_ru"] for e in enums["project_statuses"]}
    assert by_code["idea"] == "Идея"
    by_prio = {e["code"]: e["title_ru"] for e in enums["priorities"]}
    assert by_prio[1] == "Критичный"
