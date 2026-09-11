"""Соединения с SQLite: единый PRAGMA-набор, row_factory, короткие транзакции."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

BUSY_TIMEOUT_MS = 5000


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Новое соединение. Вызывается на каждый запрос / вызов инструмента.

    PRAGMA foreign_keys — НЕ персистентная настройка, включается на каждом
    соединении. journal_mode=WAL персистентен, но повторная установка безопасна.
    """
    path = Path(db_path or config.db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Короткая транзакция: открыли — записали — закрыли. Ошибка → rollback."""
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def schema_version(conn: sqlite3.Connection) -> str | None:
    """Последняя применённая миграция."""
    row = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return row["version"] if row else None


def wal_enabled(conn: sqlite3.Connection) -> bool:
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    return str(mode).lower() == "wal"
