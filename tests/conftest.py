"""Фикстуры: временна́я БД с применёнными миграциями на каждый тест + TestClient."""

import sqlite3
from pathlib import Path

import pytest

from bort import db

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "bort-test.db")
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient поверх временной БД (BORT_DB в окружении)."""
    db_file = tmp_path / "api-test.db"
    monkeypatch.setenv("BORT_DB", str(db_file))
    conn = db.connect(db_file)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.close()

    from bort.web.app import app

    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture()
def mcpcall(tmp_path, monkeypatch):
    """Временная БД + вызов MCP-инструмента через tools._call (сам открывает соединение)."""
    db_file = tmp_path / "mcp-test.db"
    monkeypatch.setenv("BORT_DB", str(db_file))
    conn = db.connect(db_file)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.close()

    from bort.mcp import tools as mcp_tools

    return mcp_tools._call
