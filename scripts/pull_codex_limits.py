"""MacBook -> mini: лимиты подписки GPT через локальный Codex CLI.

Читает официальный app-server (`account/rateLimits/read`) и отправляет снимок
на mini, чтобы вкладка AI показывала лимиты без установки Codex CLI на mini.
Так же, как `pull_qwencloud_usage.py`, использует существующий SSH по ключу.

Запуск (MacBook): python3 scripts/pull_codex_limits.py
Переменные: BORT_BEADS_SYNC_HOST / BORT_BEADS_SYNC_HOST_KEY_ALIAS (адрес mini).
"""

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from bort.services import ai_providers  # noqa: E402


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_host(value: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith("-") or any(ch.isspace() for ch in value):
        return ""
    return value


def main() -> int:
    host = _sanitize_host(os.environ.get("BORT_QWENCLOUD_SYNC_HOST") or os.environ.get("BORT_BEADS_SYNC_HOST", ""))
    key_alias = _sanitize_host(
        os.environ.get("BORT_QWENCLOUD_SYNC_HOST_KEY_ALIAS") or os.environ.get("BORT_BEADS_SYNC_HOST_KEY_ALIAS", "")
    )
    if not host:
        print("BORT_BEADS_SYNC_HOST не настроен", file=sys.stderr)
        return 1

    snapshot = ai_providers.codex_limits()
    if snapshot.get("status") != "available":
        # Один повторный заход: app-server изредка не отвечает с первого раза
        # (особенно после пробуждения Mac), а затирать хороший снимок нельзя.
        time.sleep(2)
        snapshot = ai_providers.codex_limits()
    document = {
        "version": 1,
        "captured_at": snapshot.get("captured_at") or snapshot.get("checked_at") or _iso(),
        "status": snapshot.get("status", "error"),
        "message": snapshot.get("message"),
        "data": snapshot.get("data"),
    }
    payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
    remote = (
        "import os,pathlib,sys,tempfile; "
        "root=pathlib.Path.home()/\"bort/data\"; root.mkdir(parents=True,exist_ok=True); "
        "fd,name=tempfile.mkstemp(prefix=\".codex-limits-\",dir=root); "
        "f=os.fdopen(fd,\"wb\"); f.write(sys.stdin.buffer.read()); f.close(); "
        "os.chmod(name,0o600); os.replace(name,root/\"codex-limits.json\")"
    )
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if key_alias:
        command.extend(["-o", f"HostKeyAlias={key_alias}"])
    try:
        subprocess.run([*command, host, "python3 -c " + shlex.quote(remote)],
                       input=payload, timeout=25, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Не удалось отправить снимок ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"Снимок Codex отправлен: {document['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
