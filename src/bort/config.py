"""Конфигурация из переменных окружения (.env не парсим — PM2/env задаёт значения)."""

import os
from pathlib import Path

DEFAULT_DB = Path.home() / "bort" / "data" / "bort.db"
DEFAULT_TZ = "Europe/Moscow"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
BASE_CURRENCY = "RUB"


def db_path() -> str:
    return os.environ.get("BORT_DB", str(DEFAULT_DB))


def tz_name() -> str:
    return os.environ.get("BORT_TZ", DEFAULT_TZ)


def host() -> str:
    return os.environ.get("BORT_HOST", DEFAULT_HOST)


def port() -> int:
    return int(os.environ.get("BORT_PORT", str(DEFAULT_PORT)))
