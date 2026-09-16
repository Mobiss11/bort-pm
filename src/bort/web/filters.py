"""Jinja-фильтры: деньги, даты, русские подписи.

Подписи статусов/приоритетов берутся из одного источника — ENUMS в api/meta.py
(тот же словарь отдаёт /api/v1/meta/enums).
"""

from .. import dates, money
from .api.meta import ENUMS

STATE_TITLES = {
    "overdue": "Просрочен",
    "hot": "Горит",
    "soon": "Скоро дедлайн",
    "normal": "",
    "none": "",
}


def rub(minor, currency: str = "RUB") -> str:
    """15000050 → «150 000,50 ₽»."""
    try:
        return money.format_rub(int(minor), currency)
    except (TypeError, ValueError):
        return "—"


def rub_short(minor, currency: str = "RUB") -> str:
    """Компактные деньги для плотных таблиц: «150 000 ₽», копейки — только если они есть.

    В сводке десятки сумм в столбик, и «,00» в каждой мешает их сравнивать глазом.
    """
    try:
        value = int(minor)
    except (TypeError, ValueError):
        return "—"
    full = money.format_rub(value, currency)
    return full.replace(",00", "", 1) if value % 100 == 0 else full


def date_ru(value) -> str:
    """«2026-09-20» → «20.09.2026»; пустое → «—»."""
    try:
        d = dates.parse_date(value)
    except ValueError:
        return "—"
    return d.strftime("%d.%m.%Y") if d else "—"


def datetime_ru(value) -> str:
    """ISO UTC → «09.09.2026 13:45»."""
    if not value:
        return "—"
    s = str(value)
    try:
        return f"{s[8:10]}.{s[5:7]}.{s[0:4]} {s[11:16]}"
    except IndexError:
        return s


def days_left_str(value, today=None) -> str:
    """Дней до дедлайна: «сегодня», «+3 дн», «−2 дн», «—»."""
    try:
        dl = dates.days_left(value, today)
    except ValueError:
        return "—"
    if dl is None:
        return "—"
    if dl == 0:
        return "сегодня"
    sign = "+" if dl > 0 else "−"
    return f"{sign}{abs(dl)} дн"


def label(code, group: str) -> str:
    """Русская подпись кода из справочника ENUMS.

    Вызов в шаблоне: {{ t.status|label('task_statuses') }} — Jinja передаёт
    значение первым аргументом, группу вторым.
    """
    for item_code, item_title in ENUMS.get(group, []):
        if item_code == code:
            return item_title
    return str(code) if code is not None else "—"


def state_title(state: str) -> str:
    return STATE_TITLES.get(state, "")


def dl_state(value) -> str:
    """Класс состояния дедлайна задачи: overdue/hot/soon/normal/none."""
    try:
        return dates.deadline_state(value)
    except ValueError:
        return "none"


def progress(done, total) -> str:
    """«5/12»; задач нет → «—» (проект без задач — норма, не 0%)."""
    if not total:
        return "—"
    return f"{done}/{total}"


def plural_ru(n, one: str, few: str, many: str) -> str:
    """Русское окончание по числу: 1 внесение, 2 внесения, 5 внесений."""
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        return many
    if n % 100 in range(11, 15):
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def pct(done, total):
    """Процент прогресса или None, если задач нет."""
    if not total:
        return None
    return round(int(done) / int(total) * 100)


def url_query(path: str, params: dict | None = None) -> str:
    """Собрать внутренний URL: path + query, где КАЖДОЕ значение закодировано.

    Нужен, чтобы & / # / + / кавычки / кириллица в поиске не ломали query.
    """
    from urllib.parse import urlencode

    clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    qs = urlencode(clean)
    return f"{path}?{qs}" if qs else path


def return_to(path: str, params: dict | None = None) -> str:
    """Значение параметра return_to: внутренний URL, закодированный целиком,
    чтобы его можно было безопасно вложить как значение query-параметра."""
    from urllib.parse import quote

    return quote(url_query(path, params), safe="")


def register_filters(env) -> None:
    env.filters["rub"] = rub
    env.filters["rub_short"] = rub_short
    env.filters["date_ru"] = date_ru
    env.filters["datetime_ru"] = datetime_ru
    env.filters["days_left_str"] = days_left_str
    env.filters["label"] = label
    env.filters["state_title"] = state_title
    env.filters["dl_state"] = dl_state
    env.filters["progress"] = progress
    env.filters["pct"] = pct
    env.filters["plural_ru"] = plural_ru
    env.filters["url_query"] = url_query
    env.filters["return_to"] = return_to
