"""Оплата видна в сводке, статус правится на месте. Изолированная БД, живые данные не трогаются."""
import re

from bort.services import projects as projects_svc
from bort.services import summary as summary_svc


def _row(html: str, name: str) -> str:
    """Разметка строки проекта по его названию."""
    for row in re.findall(r'<tr class="project-row.*?</tr>', html, re.S):
        if f">{name}</a>" in row:
            return row
    raise AssertionError(f"строка проекта «{name}» не найдена")


def _mk(client, name, *, status="active", amount=None, payments=()):
    body = {"name": name, "status": status}
    if amount is not None:
        body["deal_amount_minor"] = amount
    p = client.post("/api/v1/projects", json=body).json()
    for amt, on in payments:
        client.post(
            f"/api/v1/projects/{p['id']}/payments",
            json={"amount_minor": amt, "paid_on": on, "kind": "partial"},
        )
    return p


# --- Сколько внесено: сумма всех платежей прямо в строке ---


def test_summary_row_shows_total_paid_without_opening_anything(client):
    """Главная боль: сумма всех внесений видна сразу, без раскрытия «Ещё»."""
    _mk(client, "Трёхэтапный", amount=120_000_00,
        payments=[(30_000_00, "2026-09-01"), (20_000_00, "2026-09-05"), (10_000_00, "2026-09-09")])
    row = _row(client.get("/").text, "Трёхэтапный")
    assert 'data-label="Оплата"' in row
    assert "60 000 ₽" in row          # 30k + 20k + 10k одной суммой
    assert "из 120 000 ₽" in row
    assert "остаток 60 000 ₽" in row
    assert "50%" in row
    assert "3 внес." in row           # из скольких платежей она сложилась
    # процент не только цветом полосы: он же числом в aria
    assert 'aria-valuenow="50"' in row


def test_summary_row_payment_edge_states(client):
    _mk(client, "Ничего", amount=50_000_00)
    _mk(client, "Полностью", amount=50_000_00, payments=[(50_000_00, "2026-09-01")])
    _mk(client, "Переплата", amount=50_000_00, payments=[(60_000_00, "2026-09-01")])
    _mk(client, "БезСуммы", amount=0, payments=[(1_000_00, "2026-09-01")])
    html = client.get("/").text
    assert "остаток 50 000 ₽" in _row(html, "Ничего")
    assert "оплачено полностью" in _row(html, "Полностью")
    assert "переплата 10 000 ₽" in _row(html, "Переплата")
    assert "стоимость не задана" in _row(html, "БезСуммы")
    # полоса не выезжает за 100% при переплате
    assert 'style="width: 100%"' in _row(html, "Переплата")


def test_summary_service_exposes_payment_fields(conn):
    pid = projects_svc.create_project(conn, {"name": "Сервисный", "deal_amount_minor": 100_000_00})["id"]
    conn.execute(
        "INSERT INTO payments (project_id, amount_minor, paid_on, kind) VALUES (?, ?, ?, 'partial')",
        (pid, 25_000_00, "2026-09-02"),
    )
    conn.execute(
        "INSERT INTO payments (project_id, amount_minor, paid_on, kind) VALUES (?, ?, ?, 'prepayment')",
        (pid, 15_000_00, "2026-09-07"),
    )
    conn.commit()
    row = summary_svc.get_summary(conn, scope="open")["projects"][0]
    assert row["paid_minor"] == 40_000_00
    assert row["remaining_minor"] == 60_000_00
    assert row["payment_progress"] == 40
    assert row["payments_count"] == 2
    assert row["last_payment_on"] == "2026-09-07"
    # карточка проекта считает ровно так же — числа у человека и у ассистента совпадают
    card = summary_svc.get_project_summary(conn, pid)
    assert (card["paid_minor"], card["remaining_minor"], card["payment_progress"]) == (40_000_00, 60_000_00, 40)
    assert card["payments_total_minor"] == card["paid_minor"]


def test_portfolio_panel_leads_with_paid_and_remaining(client):
    _mk(client, "Первый", amount=100_000_00, payments=[(40_000_00, "2026-09-01")])
    html = client.get("/").text
    panel = re.search(r'<section class="portfolio".*?</section>', html, re.S).group(0)
    assert "Оплачено" in panel and "Остаток к оплате" in panel
    assert 'data-metric="paid_total_minor" data-minor="4000000"' in panel
    assert 'data-metric="remaining_total_minor" data-minor="6000000"' in panel
    assert 'aria-label="Портфель оплачен на 40%"' in panel


# --- Статус правится на месте ---


def test_status_select_is_editable_in_summary_and_card(client):
    p = _mk(client, "Статусный")
    row = _row(client.get("/").text, "Статусный")
    assert 'name="status"' in row and f'hx-post="/ui/projects/{p["id"]}/fields"' in row
    for _, title in [("idea", "Идея"), ("active", "В работе"), ("paused", "На паузе"), ("closed", "Закрыт")]:
        assert title in row
    card = client.get(f"/projects/{p['id']}").text
    head = re.search(r'<div class="project-meta".*?</div>', card, re.S).group(0)
    assert 'name="status"' in head and '"view": "card"' in head


def test_status_change_from_summary_moves_project_between_sections(client):
    p = _mk(client, "Переезжающий")
    r = client.post(f"/ui/projects/{p['id']}/fields", data={"status": "closed", "q": "", "tasks": ""})
    assert r.status_code == 200
    open_tbody = re.search(r'<tbody id="projects-tbody">(.*?)</tbody>', r.text, re.S).group(1)
    closed_tbody = re.search(r'<tbody id="projects-tbody-closed">(.*?)</tbody>', r.text, re.S).group(1)
    assert "Переезжающий" not in open_tbody
    assert "Переезжающий" in closed_tbody
    # панели портфеля и внимания переезжают вместе с ним
    assert re.search(r'<section class="portfolio"[^>]*hx-swap-oob="outerHTML"', r.text)
    assert re.search(r'<section class="attention"[^>]*hx-swap-oob="outerHTML"', r.text)
    assert client.get("/api/v1/projects/%d" % p["id"]).json()["status"] == "closed"


def test_status_change_keeps_active_filters(client):
    p = _mk(client, "Фильтруемый")
    _mk(client, "Посторонний")
    r = client.post(f"/ui/projects/{p['id']}/fields", data={"status": "paused", "q": "Фильтр", "tasks": ""})
    open_tbody = re.search(r'<tbody id="projects-tbody">(.*?)</tbody>', r.text, re.S).group(1)
    assert "Фильтруемый" in open_tbody
    assert "Посторонний" not in open_tbody
    assert "поиск «Фильтр»" in r.text


def test_status_change_from_card_returns_head(client):
    p = _mk(client, "Карточный", status="idea")
    r = client.post(f"/ui/projects/{p['id']}/fields", data={"status": "active", "view": "card"})
    assert r.status_code == 200
    assert "Карточный" in r.text and 'class="project-meta"' in r.text
    assert "projects-tbody" not in r.text


def test_invalid_status_does_not_break_the_board(client):
    p = _mk(client, "Невалидный")
    r = client.post(f"/ui/projects/{p['id']}/fields", data={"status": "нет-такого", "q": "", "tasks": ""})
    assert r.status_code == 200
    assert client.get(f"/api/v1/projects/{p['id']}").json()["status"] == "active"


# --- Смена статуса проставляет даты ---


def test_status_change_fills_empty_start_and_finish_dates(conn):
    pid = projects_svc.create_project(conn, {"name": "Датированный", "status": "idea"})["id"]
    started = projects_svc.update_project(conn, pid, {"status": "active"})
    assert started["started_on"], "переход в работу проставляет дату старта"
    assert started["finished_on"] is None
    finished = projects_svc.update_project(conn, pid, {"status": "closed"})
    assert finished["finished_on"]
    assert finished["started_on"] == started["started_on"]
    # возврат из закрытого не стирает руками поставленную дату
    reopened = projects_svc.update_project(conn, pid, {"status": "active"})
    assert reopened["finished_on"] == finished["finished_on"]


def test_status_change_does_not_overwrite_existing_dates(conn):
    pid = projects_svc.create_project(
        conn, {"name": "Свои даты", "status": "idea", "started_on": "2026-01-09"}
    )["id"]
    assert projects_svc.update_project(conn, pid, {"status": "active"})["started_on"] == "2026-01-09"
    explicit = projects_svc.update_project(
        conn, pid, {"status": "closed", "finished_on": "2026-02-02"}
    )
    assert explicit["finished_on"] == "2026-02-02"


def test_priority_change_leaves_dates_alone(conn):
    pid = projects_svc.create_project(conn, {"name": "Только приоритет", "status": "idea"})["id"]
    assert projects_svc.update_project(conn, pid, {"priority": 1})["started_on"] is None


# --- Панель денег карточки не отстаёт от платежей и затрат ---


def test_adding_payment_refreshes_card_money_panel(client):
    p = _mk(client, "Живая панель", amount=100_000_00)
    page = client.get(f"/projects/{p['id']}").text
    assert 'id="project-money"' in page
    assert "hx-swap-oob" not in re.search(r'<section[^>]*id="project-money".*?>', page, re.S).group(0)
    assert "платежей ещё не было" in page

    r = client.post(
        f"/ui/projects/{p['id']}/payments",
        data={"amount": "40 000", "kind": "prepayment", "paid_on": "2026-09-03"},
    )
    assert r.status_code == 200
    money = re.search(r'<section class="portfolio project-money"[^>]*id="project-money"[^>]*hx-swap-oob="outerHTML">.*?</section>', r.text, re.S)
    assert money, "панель денег должна приехать OOB вместе с блоком оплаты"
    assert "40 000 ₽" in money.group(0)
    assert "60 000 ₽" in money.group(0)          # остаток пересчитан
    assert "1 внесение" in money.group(0)        # окончание по числу, не «1 внесений»


def test_adding_expense_refreshes_card_money_panel(client):
    p = _mk(client, "Маржа живая", amount=100_000_00)
    r = client.post(
        f"/ui/projects/{p['id']}/expenses",
        data={"amount": "10 000", "category_code": "other", "spent_on": "2026-09-03"},
    )
    assert r.status_code == 200
    money = re.search(r'id="project-money"[^>]*hx-swap-oob="outerHTML">.*?</section>', r.text, re.S)
    assert money, "затрата меняет маржу — панель должна обновиться"
    assert "90 000 ₽" in money.group(0)          # маржа = 100 000 − 10 000


def test_payments_block_keeps_its_own_wrapper_for_repeat_swaps(client):
    """hx-swap=outerHTML по #payments-block: обёртка с id должна приходить в ответе,
    иначе второй платёж подряд некуда свопить."""
    p = _mk(client, "Дважды платим", amount=100_000_00)
    r = client.post(
        f"/ui/projects/{p['id']}/payments",
        data={"amount": "10 000", "kind": "partial", "paid_on": "2026-09-03"},
    )
    assert 'id="payments-block"' in r.text
    r2 = client.post(
        f"/ui/projects/{p['id']}/payments",
        data={"amount": "20 000", "kind": "partial", "paid_on": "2026-09-04"},
    )
    assert 'id="payments-block"' in r2.text
    assert "30 000 ₽" in r2.text


def test_plural_ru_endings():
    from bort.web.filters import plural_ru

    forms = ("внесение", "внесения", "внесений")
    got = [f"{n} {plural_ru(n, *forms)}" for n in (1, 2, 5, 11, 21, 104)]
    assert got == ["1 внесение", "2 внесения", "5 внесений", "11 внесений", "21 внесение", "104 внесения"]
