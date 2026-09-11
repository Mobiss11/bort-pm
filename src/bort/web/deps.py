"""FastAPI-зависимости: соединение с БД — одно на запрос, закрывается после ответа."""

from collections.abc import Iterator

import sqlite3

from .. import db


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()
