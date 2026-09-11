"""Идемпотентное применение миграций: смотрит schema_migrations, применяет недостающие.

Использование: uv run python scripts/migrate.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bort import db  # noqa: E402

MIGRATIONS_DIR = ROOT / "migrations"


def table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def applied_versions(conn) -> set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r["version"] for r in rows}


def main() -> int:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print(f"Миграции не найдены в {MIGRATIONS_DIR}")
        return 1

    conn = db.connect()
    try:
        applied = applied_versions(conn) if table_exists(conn, "schema_migrations") else set()

        pending = [f for f in files if f.stem not in applied]
        if not pending:
            print(f"Нечего применять: схема актуальна ({len(files)} миграций применено ранее).")
            return 0

        for path in pending:
            version = path.stem
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) "
                "VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                (version,),
            )
            conn.commit()
            print(f"Применена миграция: {version}")

        print(f"Готово. Применено сейчас: {len(pending)}, всего версий: {len(applied_versions(conn))}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
