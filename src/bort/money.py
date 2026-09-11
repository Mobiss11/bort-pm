"""Единственное место конвертации денег.

Канон хранения — INTEGER копейки. Float в схеме БД запрещён; конвертация
человеческих форматов происходит только здесь, округление half-up.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import unicodedata

_KOPEKS = Decimal(100)
_UNIT = Decimal(1)


def _is_space_like(ch: str) -> bool:
    """Любой юникод-символ, выглядящий как пробел-разделитель (включая экзотику:
    hair/figure/ideographic и прочие, что вставляет macOS, Telegram, Word)."""
    if ch == " ":
        return True
    if unicodedata.category(ch) in ("Zs", "Cc"):
        return True
    # Форматирующие пробелы без категории Zs: U+200B..U+200F и им подобные
    return 0x2000 <= ord(ch) <= 0x200F or ord(ch) == 0x00AD


def to_minor(value) -> int:
    """Человеческий ввод → копейки (int).

    Принимает: "150 000,50", "150000.50", 150000 (рубли), 0.1 (float → Decimal(str)).
    Числа трактуются как рубли; любые юникод-пробелы между цифрами удаляются,
    запятая → точка.
    """
    if value is None:
        raise ValueError("Сумма не задана")

    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(str(value))
    elif isinstance(value, str):
        s = "".join(ch for ch in value if not _is_space_like(ch))
        s = s.replace(",", ".")
        if not s:
            raise ValueError("Пустая сумма")
        try:
            d = Decimal(s)
        except InvalidOperation as e:
            raise ValueError(f"Некорректная сумма: {value!r}") from e
    else:
        raise ValueError(f"Неподдерживаемый тип суммы: {type(value).__name__}")

    return int((d * _KOPEKS).quantize(_UNIT, rounding=ROUND_HALF_UP))


def from_minor(minor: int) -> str:
    """Копейки → строка "150000.50" (канон для API-ответов)."""
    minor = int(minor)
    sign = "-" if minor < 0 else ""
    minor = abs(minor)
    return f"{sign}{minor // 100}.{minor % 100:02d}"


def format_rub(minor: int, currency: str = "RUB") -> str:
    """Копейки → человекочитаемая строка для UI: "150 000,50 ₽"."""
    minor = int(minor)
    sign = "-" if minor < 0 else ""
    minor = abs(minor)
    rub = f"{minor // 100:,}".replace(",", " ")
    out = f"{sign}{rub},{minor % 100:02d}"
    return f"{out} \u20bd" if currency == "RUB" else f"{out} {currency}"
