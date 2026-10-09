"""Фикстуры: временна́я БД с применёнными миграциями на каждый тест + TestClient."""

import os
import os
import sqlite3
from pathlib import Path

import pytest

from bort import db

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture(autouse=True)
def _no_machine_snapshot(monkeypatch):
    """Тесты не зависят от снимка на машине: на mini файл синхронизации есть,
    на MacBook — нет. Режим снимка включается только явным BORT_BEADS_SNAPSHOT."""
    from bort.services import beads

    real = beads._snapshot_path
    monkeypatch.setattr(
        beads,
        "_snapshot_path",
        lambda: real() if os.environ.get("BORT_BEADS_SNAPSHOT") else None,
    )


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch, tmp_path):
    """Ключи провайдеров не должны протекать из окружения/файла секретов в тесты."""
    for name in (
        "DEEPSEEK_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY",
        "OPENROUTER_API_KEY", "OPENROUTER_MANAGEMENT_KEY",
        "KRAUBE_SERVE_KEY",
        "BORT_ZAI_QUOTA_URL", "BORT_DEEPSEEK_BALANCE_URL", "BORT_QWENCLOUD_FILE",
        "BORT_CODEX_LIMITS_FILE", "BORT_KRAUBE_USAGE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    # Заявки на обновление лимитов Codex и кредитов Qwen не должны писаться
    # в реальный ~/bort/data.
    monkeypatch.setenv("BORT_CODEX_REQUEST_FILE", str(tmp_path / "codex-refresh.request"))
    monkeypatch.setenv("BORT_QWEN_REQUEST_FILE", str(tmp_path / "qwen-refresh.request"))


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
