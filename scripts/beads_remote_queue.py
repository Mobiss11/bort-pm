"""Мост к очереди команд mini: list/ack для sync-агента MacBook (по SSH).

Скрипт живёт на машине Борта (mini): агент забирает им команды и отмечает
результаты. Прямой доступ агента к SQLite mini не нужен.

Использование (обычно через ssh с MacBook):
    .venv/bin/python scripts/beads_remote_queue.py list
    echo '[{"id":1,"status":"done"}]' | .venv/bin/python scripts/beads_remote_queue.py ack
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bort import db  # noqa: E402
from bort.services import beads_admin  # noqa: E402

USAGE = "Использование: beads_remote_queue.py list | ack (результаты JSON на stdin)"


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("list", "ack"):
        print(USAGE, file=sys.stderr)
        return 2
    conn = db.connect()
    try:
        if argv[0] == "list":
            commands = beads_admin.pending_for_remote(conn)
            print(json.dumps({"commands": commands}, ensure_ascii=False))
            return 0
        try:
            results = json.load(sys.stdin)
        except ValueError:
            print("Ожидается JSON-массив результатов на stdin", file=sys.stderr)
            return 2
        if not isinstance(results, list):
            print("Ожидается JSON-массив результатов на stdin", file=sys.stderr)
            return 2
        updated = beads_admin.mark(conn, results)
        print(json.dumps({"ok": True, "updated": updated}))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
