"""Сводка: ручной порядок проектов (drag-and-drop), вид «Канбан», статусы
«на обсуждении» и «на проверке» — включая исключение обсуждения из денег."""

import re

import pytest

from bort import errors
from bort.services import projects as projects_svc
from bort.services import summary as summary_svc


def _mk(client, name, **extra):
    payload = {"name": name, "status": "active", "priority": 2}
    payload.update(extra)
    r = client.post("/api/v1/projects", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _open_tbody(html: str) -> str:
    return re.search(r'<tbody id="projects-tbody">(.*?)</tbody>', html, re.S).group(1)


# --- Ручной порядок: перестановка, сброс, защита от мусора ---


def test_reorder_uses_slots_and_reset_restores_auto_order(conn):
    a = projects_svc.create_project(conn, {"name": "A"})
    b = projects_svc.create_project(conn, {"name": "B"})
    c = projects_svc.create_project(conn, {"name": "C"})
    assert [p["name"] for p in summary_svc.get_summary(conn)["projects"]] == ["A", "B", "C"]

    # Переставлены только A и C: каждый встаёт на место другого, B не сдвигается.
    projects_svc.reorder_projects(conn, [c["id"], a["id"]])
    assert [p["name"] for p in summary_svc.get_summary(conn)["projects"]] == ["C", "B", "A"]
    assert projects_svc.has_manual_order(conn) is True

    projects_svc.reset_order(conn)
    assert projects_svc.has_manual_order(conn) is False
    assert [p["name"] for p in summary_svc.get_summary(conn)["projects"]] == ["A", "B", "C"]


def test_reorder_rejects_unknown_duplicate_and_empty(conn):
    a = projects_svc.create_project(conn, {"name": "A"})
    with pytest.raises(errors.ValidationError):
        projects_svc.reorder_projects(conn, [a["id"], 9999])
    with pytest.raises(errors.ValidationError):
        projects_svc.reorder_projects(conn, [a["id"], a["id"]])
    with pytest.raises(errors.ValidationError):
        projects_svc.reorder_projects(conn, [])
    with pytest.raises(errors.ValidationError):
        projects_svc.reorder_projects(conn, ["не-число"])
    assert projects_svc.has_manual_order(conn) is False


def test_reorder_endpoint_persists_and_unknown_id_is_422(client):
    a = _mk(client, "Альфа")
    b = _mk(client, "Бета")
    r = client.post("/ui/projects/reorder", data={"ids": [b["id"], a["id"]]})
    assert r.status_code == 200
    order = re.findall(r'<a class="project-link" href="/projects/(\d+)', _open_tbody(r.text))
    assert order == [str(b["id"]), str(a["id"])]
    # кнопка сброса появляется вместе с ручным порядком
    assert "Порядок проектов: вручную" in r.text
    assert 'hx-post="/ui/projects/reorder/reset"' in r.text
    # перезагрузка страницы сохраняет порядок
    assert re.findall(r'<a class="project-link" href="/projects/(\d+)', _open_tbody(client.get("/").text)) == [str(b["id"]), str(a["id"])]

    bad = client.post("/ui/projects/reorder", data={"ids": [a["id"], 9999]})
    assert bad.status_code == 422
    assert re.findall(r'<a class="project-link" href="/projects/(\d+)', _open_tbody(client.get("/").text)) == [str(b["id"]), str(a["id"])]


def test_reset_endpoint_returns_to_auto_order(client):
    a = _mk(client, "Альфа")
    b = _mk(client, "Бета")
    client.post("/ui/projects/reorder", data={"ids": [b["id"], a["id"]]})
    r = client.post("/ui/projects/reorder/reset", data={"view": "list"})
    assert r.status_code == 200
    assert "Порядок проектов: вручную" not in r.text
    html = client.get("/").text
    assert html.index("Альфа") < html.index("Бета")


def test_summary_has_drag_handle_view_switch_and_live_region(client):
    _mk(client, "Ручка")
    html = client.get("/").text
    assert 'class="drag-handle"' in html and 'draggable="true"' in html
    assert "Переместить проект «Ручка»" in html
    assert 'class="row-move"' in html
    assert 'href="/?view=kanban"' in html
    assert 'id="summary-live"' in html


# --- Вид «Канбан» в сводке ---


def test_kanban_view_renders_columns_cards_and_closed_fold(client):
    _mk(client, "КанбанОткрытый", status="active")
    _mk(client, "КанбанОбсуждение", status="discussion")
    _mk(client, "КанбанЗакрыт", status="closed")
    html = client.get("/", params={"view": "kanban"}).text
    assert 'class="summary-kanban"' in html
    assert 'data-status="active"' in html and 'data-status="discussion"' in html
    assert 'class="sboard-card' in html
    assert "КанбанОткрытый" in html and "КанбанОбсуждение" in html
    # архив остаётся свёрнутым блоком с таблицей закрытых
    assert "КанбанЗакрыт" in html and 'id="projects-tbody-closed"' in html
    # колонки идут в порядке статусов, без «Закрыт»
    codes = re.findall(r'<section class="sboard-col" data-status="([a-z_]+)"', html)
    assert codes == ["idea", "discussion", "active", "review", "payment", "paused"]
    # список по умолчанию канбан не рисует
    assert 'class="summary-kanban"' not in client.get("/").text


def test_payment_status_counts_in_money_and_stays_open(client):
    """«Ожидаем оплату» — работа сдана: деньги уже в общей сводке, проект открыт."""
    p = _mk(client, "Ждём оплату", status="payment", deal_amount_minor=200_000_00)
    assert client.get(f"/api/v1/projects/{p['id']}").json()["status"] == "payment"
    totals = client.get("/api/v1/summary").json()["portfolio_totals"]
    assert totals["deal_total_minor"] == 200_000_00
    assert totals["discussion_projects"] == []
    html = client.get("/").text
    assert "Ждём оплату" in _open_tbody(html)
    row = next(r for r in re.findall(r'<tr class="project-row.*?</tr>', html, re.S) if ">Ждём оплату</a>" in r)
    assert "Ожидаем оплату" in row and 'value="payment" selected' in row


def test_empty_summary_kanban_has_columns_and_placeholders(client):
    html = client.get("/", params={"view": "kanban"}).text
    codes = re.findall(r'<section class="sboard-col" data-status="([a-z_]+)"', html)
    assert codes == ["idea", "discussion", "active", "review", "payment", "paused"]
    assert html.count('class="sboard-empty muted"') == 6
    assert 'class="sboard-card' not in html


def test_create_project_in_kanban_view_returns_board(client):
    r = client.post(
        "/ui/projects",
        data={"name": "НовыйКанбан", "scope": "open", "q": "", "view": "kanban",
              "status": "active", "priority": "2"},
    )
    assert r.status_code == 200
    assert 'id="projects-table-box" hx-swap-oob="innerHTML"' in r.text
    oob = re.search(r'<div id="projects-table-box" hx-swap-oob="innerHTML">(.*)', r.text, re.S).group(1)
    assert 'class="summary-kanban"' in oob and "НовыйКанбан" in oob
    # форма из канбана несёт текущий вид, чтобы создание не сбрасывало доску в список
    form = client.get("/ui/projects/new", params={"view": "kanban"}).text
    assert 'name="view" value="kanban"' in form


def test_status_change_from_kanban_returns_board_with_oob_panels(client):
    p = _mk(client, "Переключаемый", status="active")
    r = client.post(
        f"/ui/projects/{p['id']}/fields",
        data={"status": "discussion", "view": "kanban", "q": "", "tasks": ""},
    )
    assert r.status_code == 200
    assert 'class="summary-kanban"' in r.text
    column = re.search(r'data-status="discussion".*?</section>', r.text, re.S).group(0)
    assert "Переключаемый" in column
    assert re.search(r'<section class="portfolio"[^>]*hx-swap-oob="outerHTML"', r.text)
    assert re.search(r'<section class="attention[^"]*"[^>]*hx-swap-oob="outerHTML"', r.text)


# --- Статусы «на обсуждении» и «на проверке» ---


def test_discussion_excluded_from_money_until_taken_to_work(client):
    p = _mk(client, "Обсуждаемый", status="discussion", deal_amount_minor=100_000_00)
    client.post(
        f"/api/v1/projects/{p['id']}/payments",
        json={"amount_minor": 10_000_00, "paid_on": "2026-09-01", "kind": "partial"},
    )
    client.post(
        f"/api/v1/projects/{p['id']}/expenses",
        json={"amount_minor": 5_000_00, "spent_on": "2026-09-01", "category_code": "other"},
    )
    totals = client.get("/api/v1/summary").json()["portfolio_totals"]
    assert totals["projects_count"] == 1  # проект виден в портфеле
    assert totals["deal_total_minor"] == 0
    assert totals["paid_total_minor"] == 0
    assert totals["expenses_total_minor"] == 0
    assert totals["margin_total_minor"] == 0
    assert totals["paid_progress"] is None
    assert [row["name"] for row in totals["discussion_projects"]] == ["Обсуждаемый"]
    # проект в списке и подписан как исключённый из денег
    html = client.get("/").text
    assert "Обсуждаемый" in _open_tbody(html)
    assert "На обсуждении — не в деньгах: Обсуждаемый" in html

    # взяли в работу — суммы появляются в деньгах
    client.patch(f"/api/v1/projects/{p['id']}", json={"status": "active"})
    totals = client.get("/api/v1/summary").json()["portfolio_totals"]
    assert totals["deal_total_minor"] == 100_000_00
    assert totals["paid_total_minor"] == 10_000_00
    assert totals["expenses_total_minor"] == 5_000_00
    assert totals["margin_total_minor"] == 95_000_00
    assert totals["discussion_projects"] == []
    assert "не в деньгах" not in client.get("/").text


def test_review_status_is_allowed_and_stays_open(client):
    p = _mk(client, "НаПроверке", status="review")
    assert client.get(f"/api/v1/projects/{p['id']}").json()["status"] == "review"
    html = client.get("/").text
    assert "НаПроверке" in _open_tbody(html)
    closed = re.search(r'<tbody id="projects-tbody-closed">(.*?)</tbody>', html, re.S).group(1)
    assert "НаПроверке" not in closed
    # статус виден в селекте строки вместе с подписью
    row = next(r for r in re.findall(r'<tr class="project-row.*?</tr>', html, re.S) if ">НаПроверке</a>" in r)
    assert "На проверке" in row and 'value="review" selected' in row
