"""Тесты затрат: валидация валюты против проекта, категории, фильтры, итоги."""

import pytest

from bort import errors
from bort.services import expenses, projects


@pytest.fixture()
def rub_project(conn):
    return projects.create_project(
        conn, {"name": "Рублёвый", "status": "active", "deal_amount_minor": 100_000_00}
    )


@pytest.fixture()
def usd_project(conn):
    return projects.create_project(
        conn,
        {"name": "Долларовый", "status": "active", "deal_amount_minor": 5_000_00, "currency": "USD"},
    )


def _expense(**overrides):
    data = {"amount_minor": 25_000_50, "spent_on": "2026-09-01", "category_code": "contractors"}
    data.update(overrides)
    return data


def test_add_expense_ok(conn, rub_project):
    e = expenses.add_expense(conn, rub_project["id"], _expense())
    assert e["project_id"] == rub_project["id"]
    assert e["amount_minor"] == 25_000_50
    assert e["currency"] == "RUB"
    assert e["category_code"] == "contractors"
    assert e["spent_on"] == "2026-09-01"


def test_add_expense_missing_project(conn):
    with pytest.raises(errors.NotFound):
        expenses.add_expense(conn, 999, _expense())


def test_add_expense_currency_mismatch_rejected(conn, rub_project, usd_project):
    with pytest.raises(errors.ValidationError):
        expenses.add_expense(conn, rub_project["id"], _expense(currency="USD"))
    with pytest.raises(errors.ValidationError):
        expenses.add_expense(conn, usd_project["id"], _expense(currency="RUB"))
    # Совпадающая валюта проходит
    e = expenses.add_expense(conn, usd_project["id"], _expense(currency="USD"))
    assert e["currency"] == "USD"


def test_add_expense_unknown_category(conn, rub_project):
    with pytest.raises(errors.ValidationError):
        expenses.add_expense(conn, rub_project["id"], _expense(category_code="rocket_fuel"))


@pytest.mark.parametrize("amount", [0, -100])
def test_add_expense_nonpositive_amount(conn, rub_project, amount):
    with pytest.raises(errors.ValidationError):
        expenses.add_expense(conn, rub_project["id"], _expense(amount_minor=amount))


def test_add_expense_bad_date(conn, rub_project):
    with pytest.raises(errors.ValidationError):
        expenses.add_expense(conn, rub_project["id"], _expense(spent_on="01.09.2026"))


def test_list_total_minor_sums_exactly(conn, rub_project):
    expenses.add_expense(conn, rub_project["id"], _expense(amount_minor=25_000_50))
    expenses.add_expense(conn, rub_project["id"], _expense(amount_minor=749, spent_on="2026-09-02"))
    expenses.add_expense(conn, rub_project["id"], _expense(amount_minor=1, spent_on="2026-09-03"))
    listing = expenses.list_expenses(conn, rub_project["id"])
    assert listing["total_minor"] == 25_000_50 + 749 + 1
    assert len(listing["items"]) == 3


def test_list_filters_category_and_dates(conn, rub_project):
    e1 = expenses.add_expense(conn, rub_project["id"], _expense(spent_on="2026-09-01"))
    e2 = expenses.add_expense(
        conn, rub_project["id"], _expense(amount_minor=500, spent_on="2026-09-05", category_code="ads")
    )
    expenses.add_expense(
        conn, rub_project["id"], _expense(amount_minor=300, spent_on="2026-08-20", category_code="other")
    )

    by_cat = expenses.list_expenses(conn, rub_project["id"], category_code="ads")
    assert {e["id"] for e in by_cat["items"]} == {e2["id"]}
    assert by_cat["total_minor"] == 500

    by_range = expenses.list_expenses(conn, rub_project["id"], date_from="2026-09-01", date_to="2026-09-30")
    assert {e["id"] for e in by_range["items"]} == {e1["id"], e2["id"]}

    empty = expenses.list_expenses(conn, rub_project["id"], date_from="2027-01-01")
    assert empty["items"] == [] and empty["total_minor"] == 0

    with pytest.raises(errors.ValidationError):
        expenses.list_expenses(conn, rub_project["id"], date_from="вчера")


def test_update_expense_amount_and_comment(conn, rub_project):
    e = expenses.add_expense(conn, rub_project["id"], _expense())
    updated = expenses.update_expense(conn, e["id"], {"amount_minor": 30_000_00, "comment": "Правка"})
    assert updated["amount_minor"] == 30_000_00
    assert updated["comment"] == "Правка"
    assert expenses.list_expenses(conn, rub_project["id"])["total_minor"] == 30_000_00


def test_update_expense_currency_mismatch(conn, rub_project):
    e = expenses.add_expense(conn, rub_project["id"], _expense())
    with pytest.raises(errors.ValidationError):
        expenses.update_expense(conn, e["id"], {"currency": "USD"})


def test_update_expense_unknown_category(conn, rub_project):
    e = expenses.add_expense(conn, rub_project["id"], _expense())
    with pytest.raises(errors.ValidationError):
        expenses.update_expense(conn, e["id"], {"category_code": "nope"})


def test_update_expense_to_project_currency_allowed(conn, usd_project):
    e = expenses.add_expense(conn, usd_project["id"], _expense(currency="USD"))
    updated = expenses.update_expense(conn, e["id"], {"category_code": "subscriptions"})
    assert updated["category_code"] == "subscriptions"


def test_delete_expense(conn, rub_project):
    e = expenses.add_expense(conn, rub_project["id"], _expense())
    expenses.delete_expense(conn, e["id"])
    with pytest.raises(errors.NotFound):
        expenses.get_expense(conn, e["id"])
    assert expenses.list_expenses(conn, rub_project["id"])["total_minor"] == 0


def test_missing_expense_raises_not_found(conn, rub_project):
    with pytest.raises(errors.NotFound):
        expenses.get_expense(conn, 999)
