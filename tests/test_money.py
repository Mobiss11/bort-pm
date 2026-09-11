"""Тесты money.py: парсинг человеческих форматов → копейки, регрессионный сторож float."""

import pytest

from bort import money
from bort.money import format_rub, from_minor, to_minor


def test_to_minor_spaces_and_comma():
    assert to_minor("150 000,50") == 15000050


def test_to_minor_nonbreaking_space():
    assert to_minor("150\u00a0000,50") == 15000050


def test_float_regression_guard():
    # 0.1 + 0.2 != 0.3 во float; в копейках должно быть точно
    assert to_minor(0.1) + to_minor(0.2) == to_minor(0.3)


def test_int_is_rubles():
    assert to_minor(150000) == 15000000


def test_string_dot_decimal():
    assert to_minor("150000.50") == 15000050


def test_string_plain_kopeks_input():
    assert to_minor("1250") == 125000


def test_rounding_half_up():
    assert to_minor("10.555") == 1056
    assert to_minor("2.675") == 268
    assert to_minor("0,005") == 1


def test_zero():
    assert to_minor(0) == 0
    assert to_minor("0,00") == 0


def test_negative_allowed_at_parse_level():
    # Проверки > 0 / >= 0 — на уровне БД и сервисов
    assert to_minor("-150,50") == -15050


@pytest.mark.parametrize("bad", ["", "   ", "abc", "1,2,3", "12.34.56", None, "150 000,5o"])
def test_invalid_input_raises(bad):
    with pytest.raises(ValueError):
        to_minor(bad)


def test_from_minor():
    assert from_minor(15000050) == "150000.50"
    assert from_minor(0) == "0.00"
    assert from_minor(5) == "0.05"
    assert from_minor(-5) == "-0.05"
    assert from_minor(100) == "1.00"


def test_format_rub():
    assert format_rub(15000050) == "150 000,50 \u20bd"
    assert format_rub(0) == "0,00 \u20bd"
    assert format_rub(100000, "USD") == "1 000,00 USD"
    assert format_rub(-250) == "-2,50 \u20bd"


def test_roundtrip():
    assert from_minor(to_minor("150 000,50")) == "150000.50"
