"""Тесты dates.py: строгий парсинг, days_left, deadline_state на границах."""

from datetime import date, timedelta

import pytest

from bort.dates import days_left, deadline_state, parse_date, today_local

TODAY = date(2026, 9, 9)


@pytest.mark.parametrize(
    "offset, expected",
    [
        (-1, "overdue"),
        (0, "hot"),
        (3, "hot"),
        (4, "soon"),
        (14, "soon"),
        (15, "normal"),
    ],
)
def test_deadline_state_boundaries(offset, expected):
    d = TODAY + timedelta(days=offset)
    assert deadline_state(d, TODAY) == expected


def test_deadline_state_none():
    assert deadline_state(None, TODAY) == "none"


def test_deadline_state_accepts_strings():
    assert deadline_state("2026-09-12", "2026-09-09") == "hot"      # +3
    assert deadline_state("2026-09-08", "2026-09-09") == "overdue"  # -1
    assert deadline_state("2026-09-24", "2026-09-09") == "normal"   # +15


def test_deadline_state_without_today_uses_type():
    # Без явного today — детерминированно проверяем только тип ответа
    assert deadline_state("2020-01-01") in {"overdue", "hot", "soon", "normal", "none"}


def test_days_left():
    assert days_left("2026-09-12", TODAY) == 3
    assert days_left(TODAY, TODAY) == 0
    assert days_left("2026-09-08", TODAY) == -1
    assert days_left(None, TODAY) is None


def test_parse_date_valid():
    assert parse_date("2026-09-09") == date(2026, 9, 9)
    assert parse_date(None) is None
    assert parse_date(date(2026, 9, 9)) == date(2026, 9, 9)


@pytest.mark.parametrize("bad", ["2026-9-9", "09-09-2026", "2026-13-01", "2026-02-30", "", "20260909"])
def test_parse_date_invalid(bad):
    with pytest.raises(ValueError):
        parse_date(bad)


def test_today_local_returns_date():
    assert isinstance(today_local(), date)
    assert isinstance(today_local("UTC"), date)
