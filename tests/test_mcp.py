"""Тесты MCP: прямые вызовы всех 14 инструментов + побайтовая сверка с REST API."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from bort.mcp import tools as mcp
from bort.mcp.server import TOOLS_COUNT, create_server

REPO_ROOT = Path(__file__).resolve().parent.parent


def _seed_base(mcpcall):
    r = mcpcall(mcp._project_create, name="Сайт", deal_amount="150 000,50", deadline="2026-09-20")
    assert r["ok"] is True
    return r["project"]["id"]


# --- 1. bort_project_list ---


def test_project_list(mcpcall):
    _seed_base(mcpcall)
    mcpcall(mcp._project_create, name="Долларовый", currency="USD")
    r = mcpcall(mcp._project_list)
    assert r["ok"] is True and r["total"] == 2
    assert {p["name"] for p in r["items"]} == {"Сайт", "Долларовый"}
    # Человекочитаемое поле рядом с каноном
    site = next(p for p in r["items"] if p["name"] == "Сайт")
    assert site["deal_amount_minor"] == 15_000_050 and site["deal_amount"] == "150000.50"

    r = mcpcall(mcp._project_list, q="доллар")
    assert r["total"] == 1
    r = mcpcall(mcp._project_list, priority=9)
    assert r["total"] == 0


# --- 2. bort_project_get ---


def test_project_get_by_id_and_name(mcpcall):
    pid = _seed_base(mcpcall)
    r = mcpcall(mcp._project_get, name="сайт")  # регистронезависимо
    assert r["ok"] is True
    assert r["project"]["id"] == pid
    assert r["tasks"] == [] and r["chats"] == [] and r["people"] == []

    r = mcpcall(mcp._project_get, project_id=pid)
    assert r["ok"] is True

    r = mcpcall(mcp._project_get, project_id=999)
    assert r["ok"] is False
    assert r["error"]["code"] == "not_found"


def test_project_get_ambiguous_name_conflict(mcpcall):
    _seed_base(mcpcall)
    mcpcall(mcp._project_create, name="дубль")
    mcpcall(mcp._project_create, name="Дубль")
    r = mcpcall(mcp._project_get, name="Дубль")
    assert r["ok"] is False
    assert r["error"]["code"] == "conflict"
    assert len(r["error"]["details"]["candidates"]) == 2


def test_project_get_requires_id_or_name(mcpcall):
    r = mcpcall(mcp._project_get)
    assert r["ok"] is False and r["error"]["code"] == "validation"


# --- 3. bort_project_create ---


def test_project_create(mcpcall):
    r = mcpcall(mcp._project_create, name="Бот", deal_amount="50 000", priority=1, status="active")
    assert r["ok"] is True
    p = r["project"]
    assert p["deal_amount_minor"] == 5_000_000 and p["deal_amount"] == "50000.00"
    assert p["status"] == "active" and p["priority"] == 1 and p["currency"] == "RUB"

    # minor-канон приоритетнее: обе формы сразу — ошибка
    r = mcpcall(mcp._project_create, name="X", deal_amount="1", deal_amount_minor=100)
    assert r["ok"] is False and r["error"]["code"] == "validation"


# --- 4. bort_project_update ---


def test_project_update_by_name(mcpcall):
    _seed_base(mcpcall)
    r = mcpcall(mcp._project_update, name="Сайт", status="paused", deal_amount="200 000")
    assert r["ok"] is True
    assert r["project"]["status"] == "paused"
    assert r["project"]["deal_amount_minor"] == 20_000_000

    r = mcpcall(mcp._project_update, name="Нет такого")
    assert r["ok"] is False and r["error"]["code"] == "not_found"


# --- 5–7. bort_task_create / bort_task_update / bort_task_close ---


def test_task_lifecycle(mcpcall):
    _seed_base(mcpcall)
    r = mcpcall(mcp._task_create, project_name="Сайт", title="Сверстать", priority=1, deadline="2026-09-12")
    assert r["ok"] is True
    task = r["task"]
    assert task["status"] == "todo" and task["priority"] == 1

    r = mcpcall(mcp._task_update, task_id=task["id"], status="in_progress")
    assert r["ok"] is True and r["task"]["status"] == "in_progress"

    r = mcpcall(mcp._task_close, task_id=task["id"])
    assert r["ok"] is True
    assert r["task"]["status"] == "done" and r["task"]["closed_at"] is not None

    r = mcpcall(mcp._task_close, task_id=task["id"], outcome="отменить")
    assert r["ok"] is False and r["error"]["code"] == "validation"

    r = mcpcall(mcp._task_update, task_id=999)
    assert r["ok"] is False and r["error"]["code"] == "not_found"


# --- 8. bort_expense_add ---


def test_expense_add_returns_new_margin(mcpcall):
    _seed_base(mcpcall)
    r = mcpcall(mcp._expense_add, project_name="Сайт", amount="25 000,50", category_code="contractors")
    assert r["ok"] is True
    assert r["expense"]["amount_minor"] == 2_500_050
    assert r["expense"]["amount"] == "25000.50"
    assert r["expense"]["spent_on"]  # по умолчанию — сегодня
    # Новая маржа: 15000050 − 2500050 = 12500000
    assert r["project_margin_minor"] == 12_500_000
    assert r["project_margin"] == "125000.00"

    # Чужая валюта → ошибка валидации, не исключение
    r = mcpcall(mcp._expense_add, project_name="Сайт", amount_minor=100, currency="USD")
    assert r["ok"] is False and r["error"]["code"] == "validation"

    # Нет суммы вовсе
    r = mcpcall(mcp._expense_add, project_name="Сайт")
    assert r["ok"] is False and r["error"]["code"] == "validation"


# --- 9–10. bort_summary / bort_project_summary ---


def test_summary_and_project_summary(mcpcall):
    _seed_base(mcpcall)
    mcpcall(mcp._expense_add, project_name="Сайт", amount_minor=500)
    mcpcall(mcp._project_create, name="Долларовый", currency="USD", deal_amount_minor=100_000)

    r = mcpcall(mcp._summary)
    assert r["ok"] is True
    t = r["totals"]
    assert t["projects_count"] == 1
    assert t["deal_total_minor"] == 15_000_050
    assert t["expenses_total_minor"] == 500
    assert t["margin_total_minor"] == 15_000_050 - 500
    assert t["excluded_projects"][0]["name"] == "Долларовый"
    assert t["deal_total"] == "150000.50"

    r = mcpcall(mcp._project_summary, name="Сайт")
    assert r["ok"] is True
    s = r["summary"]
    assert s["margin_minor"] == 15_000_050 - 500
    assert s["expenses_by_category"] == [
        {"category_code": "other", "total_minor": 500, "total": "5.00", "count": 1}
    ]
    # Платежей пока нет: оплачено 0, прогресс 0%
    assert s["payments_total_minor"] == 0
    assert s["payment_progress"] == 0
    assert s["remaining_minor"] == 15_000_050
    assert s["remaining"] == "150000.50"

    # Платёж меняет оплату в MCP
    mcpcall(mcp._project_create, name="Для платежа", deal_amount_minor=1_000_00)
    mcpcall(mcp._project_summary, name="Для платежа")
    # (создание платежа идёт через REST/сервисы; тут проверяем только чтение из сводки)
    pg = mcpcall(mcp._project_get, name="Для платежа")
    assert pg["ok"] is True
    assert pg["payments_total_minor"] == 0 and pg["payment_progress"] == 0
    assert isinstance(pg["payments"], list)

    r = mcpcall(mcp._project_summary, name="Нет такого")
    assert r["ok"] is False and r["error"]["code"] == "not_found"


# --- 11–12. bort_chat_attach / bort_chat_detach ---


def test_chat_attach_detach(mcpcall):
    pid = _seed_base(mcpcall)
    pid2 = mcpcall(mcp._project_create, name="Второй")["project"]["id"]

    # Создание чата прямо при привязке
    r = mcpcall(mcp._chat_attach, project_name="Сайт", title="Рабочая группа", tg_chat_id="-100123", note="оперативка")
    assert r["ok"] is True
    chat = r["chat"]
    assert any(p["id"] == pid for p in chat["projects"])

    # Один чат — на два проекта
    r = mcpcall(mcp._chat_attach, project_id=pid2, chat_id=chat["id"])
    assert r["ok"] is True
    assert len(r["chat"]["projects"]) == 2

    # Повторная привязка → conflict
    r = mcpcall(mcp._chat_attach, project_id=pid2, chat_id=chat["id"])
    assert r["ok"] is False and r["error"]["code"] == "conflict"

    r = mcpcall(mcp._chat_detach, project_id=pid, chat_id=chat["id"])
    assert r["ok"] is True and r["detached"] is True

    # Чат пережил отвязку и остался у второго проекта
    r = mcpcall(mcp._chat_list, project_id=pid2)
    assert [c["id"] for c in r["chats"]] == [chat["id"]]

    # Повторная отвязка → not_found
    r = mcpcall(mcp._chat_detach, project_id=pid, chat_id=chat["id"])
    assert r["ok"] is False and r["error"]["code"] == "not_found"


# --- 13. bort_chat_list ---


def test_chat_list(mcpcall):
    _seed_base(mcpcall)
    mcpcall(mcp._chat_attach, project_name="Сайт", title="Альфа", tg_chat_id="111")
    mcpcall(mcp._chat_attach, project_name="Сайт", title="Бета", tg_chat_id="222")

    r = mcpcall(mcp._chat_list)
    assert r["ok"] is True and len(r["chats"]) == 2
    assert all("members" in c and "projects" in c for c in r["chats"])

    r = mcpcall(mcp._chat_list, q="альфа")
    assert len(r["chats"]) == 1 and r["chats"][0]["title"] == "Альфа"


# --- 14. bort_person_upsert ---


def test_person_upsert(mcpcall):
    pid = _seed_base(mcpcall)
    chat_r = mcpcall(mcp._chat_attach, project_name="Сайт", title="Чат проекта")
    chat_id = chat_r["chat"]["id"]

    r = mcpcall(
        mcp._person_upsert,
        full_name="Иван Иванов",
        tg_username="@ivan",
        attach_to_project_id=pid,
        attach_to_chat_id=chat_id,
        role="заказчик",
    )
    assert r["ok"] is True and r["created"] is True
    person = r["person"]
    assert person["tg_username"] == "ivan"  # @ снят
    assert r["attached_project_id"] == pid and r["attached_chat_id"] == chat_id

    # Повторный upsert — тот же человек, обновление полей и роли существующих привязок
    r2 = mcpcall(
        mcp._person_upsert,
        tg_username="Ivan",
        notes="важный заказчик",
        attach_to_project_id=pid,
        attach_to_chat_id=chat_id,
        role="главный",
    )
    assert r2["ok"] is True and r2["created"] is False
    assert r2["person"]["id"] == person["id"]
    assert r2["person"]["notes"] == "важный заказчик"
    assert r2["attached_project_id"] == pid and r2["attached_chat_id"] == chat_id
    assert len(r2["person"]["projects"]) == 1
    assert r2["person"]["projects"][0]["role"] == "главный"
    assert r2["person"]["chats"][0]["role"] == "главный"


# --- Ресурс и сервер ---


def test_summary_markdown(conn):
    from bort.services import projects as projects_svc

    projects_svc.create_project(conn, {"name": "Сайт", "deadline": "2026-09-20", "deal_amount_minor": 100_000})
    text = mcp.summary_markdown(conn)
    assert text.startswith("# Борт — сводка")
    assert "Сайт" in text
    assert "| Проект | Статус |" in text
    assert "1 000,00" in text


def test_server_registers_all_tools():
    server = create_server()
    listed = asyncio.run(server.list_tools())
    names = {t.name for t in listed}
    expected = {
        "bort_project_list", "bort_project_get", "bort_project_create", "bort_project_update",
        "bort_task_create", "bort_task_update", "bort_task_close",
        "bort_expense_add", "bort_summary", "bort_project_summary",
        "bort_chat_attach", "bort_chat_detach", "bort_chat_list", "bort_person_upsert",
    }
    assert names == expected
    assert len(names) == TOOLS_COUNT

    resources = asyncio.run(server.list_resources())
    assert any(str(getattr(r, "uri", r)) == "bort://summary" for r in resources)


def test_stdio_initialize(tmp_path):
    """python -m bort.mcp.server отвечает на initialize по stdio (line-delimited JSON-RPC)."""
    db_file = tmp_path / "stdio.db"
    env = {**os.environ, "BORT_DB": str(db_file)}
    subprocess.run(
        [sys.executable, "scripts/migrate.py"],
        capture_output=True, text=True, timeout=60, env=env, cwd=REPO_ROOT, check=True,
    )
    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "acceptance", "version": "0"},
        },
    }
    proc = subprocess.run(
        [sys.executable, "-m", "bort.mcp.server"],
        input=json.dumps(init) + "\n",
        capture_output=True, text=True, timeout=60, env=env, cwd=REPO_ROOT,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"нет ответа; stderr: {proc.stderr[:500]}"
    resp = json.loads(lines[0])
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "bort"


# --- Ключевой критерий приёмки: побайтовая сверка bort_summary и REST /api/v1/summary ---


def test_mcp_summary_matches_rest_byte_for_byte(client, tmp_path, monkeypatch):
    # client уже выставил BORT_DB на временную БД — MCP прочитает тот же файл
    client.post("/api/v1/projects", json={"name": "Сайт", "deal_amount": "150 000,50", "deadline": "2026-09-20"})
    r = client.post("/api/v1/projects", json={"name": "Долларовый", "currency": "USD", "deal_amount_minor": 100_000})
    pid = r.json()["id"]
    task = client.post(f"/api/v1/projects/{pid}/tasks", json={"title": "T"}).json()
    client.post(f"/api/v1/tasks/{task['id']}/close", json={"status": "done"})

    rest = client.get("/api/v1/summary").json()
    mcp_result = mcp._call(mcp._summary, scope="open")
    assert mcp_result["ok"] is True

    rest_body = {"totals": rest["totals"], "projects": rest["projects"]}
    mcp_body = {k: mcp_result[k] for k in ("totals", "projects")}

    # Побайтово: идентичные JSON-строки
    assert (
        json.dumps(rest_body, sort_keys=True, ensure_ascii=False)
        == json.dumps(mcp_body, sort_keys=True, ensure_ascii=False)
    )
