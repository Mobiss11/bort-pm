"""Даты: единственный источник «сегодня» — today_local() в BORT_TZ.

Дедлайн — календарная дата YYYY-MM-DD без времени и TZ. julianday('now') в SQL
не используется: он вернёт UTC-дату и вечером даст сдвиг на день.
"""

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import config

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Пороги классификации дедлайна (дней до него)
HOT_MAX = 3    # 0..3  — горит
SOON_MAX = 14  # 4..14 — скоро


def today_local(tz_name: str | None = None) -> date:
    """«Сегодня» в локальной зоне (по умолчанию BORT_TZ)."""
    return datetime.now(ZoneInfo(tz_name or config.tz_name())).date()


def parse_date(value) -> date | None:
    """Строго YYYY-MM-DD → date. None пропускается."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not _DATE_RE.match(s):
        raise ValueError(f"Дата должна быть в формате YYYY-MM-DD, получено: {value!r}")
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"Некорректная дата: {value!r}") from e


def days_left(deadline, today: date | None = None) -> int | None:
    """Дней до дедлайна: 0 — сегодня, <0 — просрочен, None — дедлайна нет."""
    d = parse_date(deadline)
    if d is None:
        return None
    if today is None:
        today = today_local()
    elif isinstance(today, str):
        today = parse_date(today)
    return (d - today).days


def deadline_state(deadline, today: date | None = None) -> str:
    """Категория срочности: overdue / hot / soon / normal / none."""
    dl = days_left(deadline, today)
    if dl is None:
        return "none"
    if dl < 0:
        return "overdue"
    if dl <= HOT_MAX:
        return "hot"
    if dl <= SOON_MAX:
        return "soon"
    return "normal"
