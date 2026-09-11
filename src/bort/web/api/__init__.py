"""Транспортные помощники: деньги на границе API.

*_minor — канон; человекочитаемые суммы (строка/число) конвертируются
через money.to_minor() и обратно через money.from_minor().
"""

from ... import errors, money

MONEY_PROJECT = {"deal_amount_minor": "deal_amount"}
MONEY_EXPENSE = {"amount_minor": "amount"}
MONEY_PAYMENT = {"amount_minor": "amount"}
MONEY_SUMMARY_ROW = {
    "deal_amount_minor": "deal_amount",
    "expenses_minor": "expenses",
    "margin_minor": "margin",
    "paid_minor": "paid",
}
MONEY_PROJECT_SUMMARY = {
    **MONEY_SUMMARY_ROW,
    "remaining_minor": "remaining",
}
MONEY_TOTALS = {
    "deal_total_minor": "deal_total",
    "expenses_total_minor": "expenses_total",
    "margin_total_minor": "margin_total",
}
MONEY_CATEGORY_TOTAL = {"total_minor": "total"}


def resolve_minor(data: dict, *, minor_key: str, human_key: str) -> dict:
    """Заменяет human-сумму на *_minor. Передавать обе формы сразу — ошибка."""
    data = dict(data or {})
    human = data.get(human_key)
    if human is None:
        return data
    if data.get(minor_key) is not None:
        raise errors.ValidationError(
            f"Переданы обе формы суммы — используйте {minor_key} или {human_key}",
            details={minor_key: data[minor_key], human_key: human},
        )
    try:
        data[minor_key] = money.to_minor(human)
    except ValueError as e:
        raise errors.ValidationError(str(e), details={human_key: human}) from e
    del data[human_key]
    return data


def with_money(obj: dict, mapping: dict) -> dict:
    """Добавляет человекочитаемые строковые поля рядом с *_minor."""
    out = dict(obj)
    for minor_key, human_key in mapping.items():
        if out.get(minor_key) is not None:
            out[human_key] = money.from_minor(out[minor_key])
    return out
