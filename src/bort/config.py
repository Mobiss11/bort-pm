"""Конфигурация из переменных окружения и локального файла секретов.

Приоритет: реальное окружение важнее файла. Файл `~/.config/bort/env` хранит
ключи провайдеров вне репозитория (строки KEY=VALUE, `#` — комментарий);
путь переопределяется BORT_ENV_FILE. Загружается один раз при импорте.
"""

import os
from pathlib import Path

DEFAULT_DB = Path.home() / "bort" / "data" / "bort.db"
DEFAULT_TZ = "Europe/Moscow"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
BASE_CURRENCY = "RUB"


def default_env_file() -> Path:
    return Path(os.environ.get("BORT_ENV_FILE") or Path.home() / ".config" / "bort" / "env")


def load_env_file(path: Path | None = None) -> int:
    """Дополнить окружение парами KEY=VALUE из файла, не переопределяя заданные."""
    target = Path(path) if path is not None else default_env_file()
    try:
        raw = target.expanduser().read_text(encoding="utf-8")
    except OSError:
        return 0
    loaded = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


load_env_file()


def db_path() -> str:
    return os.environ.get("BORT_DB", str(DEFAULT_DB))


def tz_name() -> str:
    return os.environ.get("BORT_TZ", DEFAULT_TZ)


def host() -> str:
    return os.environ.get("BORT_HOST", DEFAULT_HOST)


def port() -> int:
    return int(os.environ.get("BORT_PORT", str(DEFAULT_PORT)))
