"""Инлайн-правка «Стоимости» проекта (deal_amount_minor) в панели денег карточки."""


def _mk(client, name="Проект", deal="300000"):
    r = client.post("/api/v1/projects", json={"name": name, "deal_amount": deal})
    assert r.status_code == 201
    return r.json()


def _deal_minor(client, pid):
    return client.get(f"/api/v1/projects/{pid}").json()["deal_amount_minor"]


def test_money_panel_has_edit_button(client):
    p = _mk(client)
    page = client.get(f"/projects/{p['id']}").text
    assert "Изменить стоимость проекта" in page
    assert f'/ui/projects/{p["id"]}/deal/edit' in page
    assert "<button" in page


def test_edit_form_prefilled(client):
    p = _mk(client, "Правка стоимости", deal="150000")
    r = client.get(f"/ui/projects/{p['id']}/deal/edit")
    assert r.status_code == 200
    assert 'id="project-money"' in r.text
    assert 'name="deal"' in r.text
    assert 'inputmode="decimal"' in r.text
    assert 'value="150 000"' in r.text          # человеческая форма без «,00»
    assert "Сохранить" in r.text and "Отмена" in r.text
    assert 'aria-label="Стоимость проекта"' in r.text


def test_edit_form_prefilled_with_kopeks(client):
    p = _mk(client, "С копейками", deal="150000,50")
    r = client.get(f"/ui/projects/{p['id']}/deal/edit")
    assert r.status_code == 200
    assert 'value="150 000,50"' in r.text

    p0 = _mk(client, "Ноль", deal="0")
    r0 = client.get(f"/ui/projects/{p0['id']}/deal/edit")
    assert 'value="0"' in r0.text


def test_save_valid_updates_db_and_panel(client):
    p = _mk(client, "Новая стоимость", deal="300000")
    r = client.post(f"/ui/projects/{p['id']}/deal", data={"deal": "200 000,50"})
    assert r.status_code == 200
    assert 'id="project-money"' in r.text
    assert "Стоимость" in r.text and "Остаток к оплате" in r.text
    assert "200 000,50 ₽" in r.text
    assert _deal_minor(client, p["id"]) == 20_000_050


def test_save_recomputes_derived_numbers(client):
    p = _mk(client, "Пересчёт", deal="300000")
    client.post(
        f"/api/v1/projects/{p['id']}/payments",
        json={"amount": "50 000", "paid_on": "2026-09-01", "kind": "prepayment"},
    )
    r = client.post(f"/ui/projects/{p['id']}/deal", data={"deal": "200 000,50"})
    assert r.status_code == 200
    assert "200 000,50 ₽" in r.text   # новая стоимость
    assert "150 000,50 ₽" in r.text   # остаток = 200 000,50 − 50 000
    assert "25%" in r.text            # % оплачено пересчитан
    assert "Маржа" in r.text          # маржа в том же ответе


def test_save_empty_and_zero(client):
    p = _mk(client, "Без стоимости", deal="300000")
    r = client.post(f"/ui/projects/{p['id']}/deal", data={"deal": ""})
    assert r.status_code == 200
    assert _deal_minor(client, p["id"]) == 0
    assert 'role="progressbar"' not in r.text   # для нулевой стоимости полоса скрыта

    client.post(f"/ui/projects/{p['id']}/deal", data={"deal": "1000"})
    r0 = client.post(f"/ui/projects/{p['id']}/deal", data={"deal": "0"})
    assert r0.status_code == 200
    assert _deal_minor(client, p["id"]) == 0


def test_save_invalid_keeps_value_and_shows_error(client):
    p = _mk(client, "Ошибка", deal="300000")
    for bad in ("abc", "-5"):
        r = client.post(f"/ui/projects/{p['id']}/deal", data={"deal": bad})
        assert r.status_code == 200
        assert 'role="alert"' in r.text
        assert f'value="{bad}"' in r.text
        assert _deal_minor(client, p["id"]) == 30_000_000  # ничего не сохранилось


def test_missing_project_404(client):
    assert client.get("/ui/projects/999999/deal/edit").status_code == 404
    assert client.post("/ui/projects/999999/deal", data={"deal": "100"}).status_code == 404
    assert client.get("/ui/projects/999999/money").status_code == 404


def test_deal_change_after_payment_remaining(client):
    p = _mk(client, "Платёж и правка", deal="100000")
    client.post(
        f"/api/v1/projects/{p['id']}/payments",
        json={"amount": "40 000", "paid_on": "2026-09-01", "kind": "partial"},
    )
    client.post(f"/ui/projects/{p['id']}/deal", data={"deal": "100 000,50"})
    r = client.get(f"/ui/projects/{p['id']}/money")
    assert r.status_code == 200
    assert "60 000,50 ₽" in r.text


def test_oob_money_panel_still_works_after_payment(client):
    """Панель денег приезжает OOB с платежом и содержит кнопку правки."""
    p = _mk(client, "OOB панель", deal="100000")
    r = client.post(
        f"/ui/projects/{p['id']}/payments",
        data={"amount": "10 000", "kind": "partial", "paid_on": "2026-09-03"},
    )
    assert r.status_code == 200
    assert 'id="project-money"' in r.text
    assert "Изменить стоимость проекта" in r.text
