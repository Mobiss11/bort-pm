"""Раздел «Работа»: CRUD справочника постоянных проектов, итоги «приходит в месяц»
и htmx-роуты. Раздел независим от денег «Сводки» и от Beads."""

import re

import pytest

from bort import errors
from bort.services import work as work_svc


def _mk(conn, name="Проект", **extra):
    payload = {"name": name}
    payload.update(extra)
    return work_svc.create_work_project(conn, payload)


# --- Сервис: CRUD и валидация ---


def test_crud_roundtrip(conn):
    created = _mk(
        conn,
        "Фултайм",
        client="Клиент",
        role="Разработчик",
        format="full_time",
        monthly_amount_minor=150_000_00,
        currency="RUB",
        status="active",
        started_on="2026-01-15",
        notes="Заметка",
        url="https://example.com",
    )
    assert created["id"] > 0
    assert work_svc.get_work_project(conn, created["id"])["name"] == "Фултайм"

    updated = work_svc.update_work_project(conn, created["id"], {"monthly_amount_minor": 160_000_00})
    assert updated["monthly_amount_minor"] == 160_000_00
    assert updated["client"] == "Клиент"

    work_svc.delete_work_project(conn, created["id"])
    with pytest.raises(errors.NotFound):
        work_svc.get_work_project(conn, created["id"])


def test_get_missing_raises_not_found(conn):
    with pytest.raises(errors.NotFound):
        work_svc.get_work_project(conn, 9999)


@pytest.mark.parametrize(
    "data",
    [
        {"name": "   "},
        {"name": "Валюта", "currency": "RB"},
        {"name": "Дата", "started_on": "2026-13-40"},
        {"name": "Формат", "format": "remote"},
        {"name": "Статус", "status": "archived"},
        {"name": "Сумма", "monthly_amount_minor": -1},
    ],
)
def test_create_validation_errors(conn, data):
    with pytest.raises(errors.ValidationError):
        work_svc.create_work_project(conn, data)


def test_update_validation_errors(conn):
    w = _mk(conn)
    for bad in ({"format": "remote"}, {"status": "archived"}, {"currency": "RB"}, {"started_on": "нет"}):
        with pytest.raises(errors.ValidationError):
            work_svc.update_work_project(conn, w["id"], bad)


def test_list_order_status_then_name(conn):
    _mk(conn, "zeta", status="finished")
    _mk(conn, "Beta", status="paused")
    _mk(conn, "alpha", status="active")
    _mk(conn, "Omega", status="active")
    names = [w["name"] for w in work_svc.list_work_projects(conn)]
    assert names == ["alpha", "Omega", "Beta", "zeta"]


# --- Сервис: work_totals ---


def test_work_totals_split_and_foreign(conn):
    _mk(conn, "Активный", status="active", monthly_amount_minor=100_000_00)
    _mk(conn, "Второй активный", status="active", monthly_amount_minor=50_000_00)
    _mk(conn, "Пауза", status="paused", monthly_amount_minor=20_000_00)
    _mk(conn, "Завершён", status="finished", monthly_amount_minor=5_000_00)
    _mk(conn, "Доллары", status="active", monthly_amount_minor=200_00, currency="USD")

    totals = work_svc.work_totals(conn)
    assert totals["currency"] == "RUB"
    assert totals["active_minor"] == 150_000_00  # USD в активные не подмешан
    assert totals["paused_minor"] == 20_000_00
    assert totals["counts"] == {"active": 3, "paused": 1, "finished": 1, "total": 5}
    assert [f["name"] for f in totals["foreign"]] == ["Доллары"]
    assert totals["foreign"][0]["currency"] == "USD"
    assert totals["foreign"][0]["monthly_amount_minor"] == 200_00


def test_work_totals_empty(conn):
    totals = work_svc.work_totals(conn)
    assert totals["active_minor"] == 0 and totals["paused_minor"] == 0
    assert totals["counts"] == {"active": 0, "paused": 0, "finished": 0, "total": 0}
    assert totals["foreign"] == []


# --- UI-роуты ---


def test_work_page_renders_and_nav(client):
    html = client.get("/work").text
    assert 'href="/work"' in html
    assert 'aria-current="page">Работа' in html
    assert "Приходит в месяц" in html
    assert "Пока пусто — добавьте первый проект" in html
    assert 'id="work-form-box"' in html


def test_ui_create_edit_status_delete(client):
    # создание
    r = client.post(
        "/ui/work",
        data={"name": "Новый проект", "client": "ООО", "role": "Разработчик",
              "format": "contract", "monthly": "50 000", "currency": "RUB",
              "status": "active", "started_on": "2026-02-01", "url": "", "notes": ""},
    )
    assert r.status_code == 200
    assert "Новый проект" in r.text
    assert "50 000" in r.text
    wid = int(re.search(r"/ui/work/(\d+)/edit", r.text).group(1))

    # список на странице
    page = client.get("/work").text
    assert "Новый проект" in page and "ООО" in page
    assert "50 000" in page

    # форма редактирования заполнена
    form = client.get(f"/ui/work/{wid}/edit").text
    assert 'value="Новый проект"' in form
    assert 'value="50 000"' in form
    assert 'value="contract" selected' in form

    # сохранение
    r = client.post(
        f"/ui/work/{wid}",
        data={"name": "Обновлённый", "client": "ООО", "role": "Ведущий",
              "format": "part_time", "monthly": "60 000", "currency": "RUB",
              "status": "active", "started_on": "2026-02-01", "url": "", "notes": ""},
    )
    assert r.status_code == 200
    assert "Обновлённый" in r.text
    assert "60 000" in client.get("/work").text

    # смена статуса
    r = client.post(f"/ui/work/{wid}/status", data={"status": "paused"})
    assert r.status_code == 200
    assert 'value="paused" selected' in r.text
    assert 'value="paused" selected' in client.get("/work").text

    # удаление
    r = client.delete(f"/ui/work/{wid}")
    assert r.status_code == 200
    assert "Обновлённый" not in r.text
    assert "Обновлённый" not in client.get("/work").text


def test_ui_create_validation_error_keeps_form(client):
    r = client.post("/ui/work", data={"name": "", "monthly": "", "currency": "RUB"})
    assert r.status_code == 200
    assert "form-error" in r.text
    assert "<form" in r.text


# --- Миграции ---


def test_work_migration_applied(conn):
    versions = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
    assert "009_work_projects" in versions
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'work_projects'"
    ).fetchone()
    assert table is not None