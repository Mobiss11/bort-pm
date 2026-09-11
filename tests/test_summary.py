"""Тесты сводки: порядок §3.4, deadline_state, точная маржа, исключение чужих валют."""

from datetime import date

import pytest

from bort import errors
from bort.services import expenses, projects, summary

TODAY = date(2026, 9, 9)  # все дедлайны сидируются относительно этой даты


@pytest.fixture()
def seeded(conn):
    hot = projects.create_project(
        conn,
        {
            "name": "Горящий",
            "status": "active",
            "priority": 1,
            "deadline": "2026-09-10",  # +1 день
            "deal_amount_minor": 100_000_00,
        },
    )
    overdue = projects.create_project(
        conn,
        {
            "name": "Просрочен",
            "status": "active",
            "priority": 3,
            "deadline": "2026-09-07",  # −2 дня
        },
    )
    soon = projects.create_project(
        conn,
        {"name": "Скоро", "status": "active", "priority": 2, "deadline": "2026-09-19"},  # +10
    )
    normal = projects.create_project(
        conn,
        {"name": "Обычный", "status": "active", "priority": 2, "deadline": "2026-10-19"},  # +40
    )
    no_deadline = projects.create_project(
        conn, {"name": "Без дедлайна", "status": "idea", "priority": 3}
    )
    expenses.add_expense(
        conn, hot["id"], {"amount_minor": 25_000_50, "spent_on": "2026-09-01", "category_code": "contractors"}
    )
    expenses.add_expense(
        conn, overdue["id"], {"amount_minor": 500, "spent_on": "2026-09-02", "category_code": "ads"}
    )
    return {"hot": hot, "overdue": overdue, "soon": soon, "normal": normal, "none": no_deadline}


def test_order_burning_first_then_priority_deadline(conn, seeded):
    result = summary.get_summary(conn, today=TODAY)
    names = [p["name"] for p in result["projects"]]
    assert names == ["Горящий", "Просрочен", "Скоро", "Обычный", "Без дедлайна"]


def test_deadline_states(conn, seeded):
    states = {p["name"]: p["deadline_state"] for p in summary.get_summary(conn, today=TODAY)["projects"]}
    assert states == {
        "Горящий": "hot",
        "Просрочен": "overdue",
        "Скоро": "soon",
        "Обычный": "normal",
        "Без дедлайна": "none",
    }


def test_margin_is_deal_minus_expenses_exact(conn, seeded):
    for p in summary.get_summary(conn, today=TODAY)["projects"]:
        assert p["margin_minor"] == p["deal_amount_minor"] - p["expenses_minor"]
    # И покопеечно для конкретного проекта: 100000.00 − 25000.50 = 74999.50
    hot = next(p for p in summary.get_summary(conn, today=TODAY)["projects"] if p["name"] == "Горящий")
    assert hot["margin_minor"] == 74_999_50
    # Затраты больше нуля при нулевой сделке → отрицательная маржа, без искажений
    overdue = next(p for p in summary.get_summary(conn, today=TODAY)["projects"] if p["name"] == "Просрочен")
    assert overdue["margin_minor"] == -500


def test_totals_aggregates(conn, seeded):
    totals = summary.get_summary(conn, today=TODAY)["totals"]
    assert totals["currency"] == "RUB"
    assert totals["projects_count"] == 5
    assert totals["deal_total_minor"] == 100_000_00
    assert totals["expenses_total_minor"] == 25_000_50 + 500
    assert totals["margin_total_minor"] == 74_999_50 - 500
    assert totals["overdue_count"] == 1
    assert totals["hot_count"] == 1
    assert totals["excluded_projects"] == []


def test_non_base_currency_excluded_from_totals(conn, seeded):
    usd = projects.create_project(
        conn,
        {
            "name": "Долларовый",
            "status": "active",
            "priority": 4,
            "deal_amount_minor": 100_000,
            "currency": "USD",
        },
    )
    expenses.add_expense(
        conn, usd["id"], {"amount_minor": 500, "currency": "USD", "spent_on": "2026-09-01", "category_code": "other"}
    )

    result = summary.get_summary(conn, today=TODAY)
    totals = result["totals"]

    assert totals["projects_count"] == 5
    assert totals["deal_total_minor"] == 100_000_00
    assert totals["expenses_total_minor"] == 25_000_50 + 500
    assert totals["margin_total_minor"] == 74_999_50 - 500
    assert totals["excluded_projects"] == [{"id": usd["id"], "name": "Долларовый", "currency": "USD"}]

    # USD-проект в списке сводки присутствует, но в деньгах totals не участвует
    assert any(p["id"] == usd["id"] for p in result["projects"])


def test_portfolio_totals_include_closed(conn, seeded):
    """Портфельные тоталы (вверху сводки) считают открытые + закрытые вместе."""
    projects.update_project(conn, seeded["hot"]["id"], {"status": "closed"})
    expenses.add_expense(
        conn, seeded["overdue"]["id"], {"amount_minor": 2_000, "spent_on": "2026-09-03", "category_code": "ads"}
    )

    result = summary.get_summary(conn, today=TODAY)
    totals, portfolio = result["totals"], result["portfolio_totals"]

    # открытые: 4 проекта, без закрытого «Горящего» (сумма была только у него)
    assert totals["projects_count"] == 4
    assert totals["deal_total_minor"] == 0
    assert totals["margin_total_minor"] == -(500 + 2_000)

    # портфель: все 5, «Горящий» не выпал из маржи
    assert portfolio["projects_count"] == 5
    assert portfolio["deal_total_minor"] == 100_000_00
    assert portfolio["expenses_total_minor"] == 25_000_50 + 500 + 2_000
    assert portfolio["margin_total_minor"] == 100_000_00 - (25_000_50 + 500 + 2_000)
    assert portfolio["closed_count"] == 1
    # чужих валют в сиде нет — исключения пустые
    assert portfolio["excluded_projects"] == []


def test_scope_filters(conn, seeded):
    projects.update_project(conn, seeded["hot"]["id"], {"status": "closed"})

    open_names = [p["name"] for p in summary.get_summary(conn, scope="open", today=TODAY)["projects"]]
    assert "Горящий" not in open_names
    assert len(open_names) == 4

    active_names = [p["name"] for p in summary.get_summary(conn, scope="active", today=TODAY)["projects"]]
    assert active_names == ["Просрочен", "Скоро", "Обычный"]

    all_names = [p["name"] for p in summary.get_summary(conn, scope="all", today=TODAY)["projects"]]
    # Горящий (закрыт, но дедлайн +1 день и приоритет 1) всё ещё в «горящей» группе — сортировка §3.4 не смотрит статус
    assert all_names == ["Горящий", "Просрочен", "Скоро", "Обычный", "Без дедлайна"]

    with pytest.raises(errors.ValidationError):
        summary.get_summary(conn, scope="everything")


def test_summary_q_filter(conn, seeded):
    result = summary.get_summary(conn, q="горя", today=TODAY)
    assert [p["name"] for p in result["projects"]] == ["Горящий"]
    assert result["totals"]["projects_count"] == 1


def test_project_summary_with_category_breakdown(conn, seeded):
    s = summary.get_project_summary(conn, seeded["hot"]["id"], today=TODAY)
    assert s["name"] == "Горящий"
    assert s["deadline_state"] == "hot"
    assert s["deal_amount_minor"] == 100_000_00
    assert s["expenses_minor"] == 25_000_50
    assert s["margin_minor"] == 74_999_50
    assert s["expenses_by_category"] == [
        {"category_code": "contractors", "total_minor": 25_000_50, "count": 1}
    ]


def test_project_summary_missing_raises(conn, seeded):
    with pytest.raises(errors.NotFound):
        summary.get_project_summary(conn, 999)
