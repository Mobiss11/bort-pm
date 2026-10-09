"""MacBook <-> mini: снимок Beads, команды из интерфейса и лимиты Codex.

Запускается LaunchAgent на MacBook (там есть bd и каталоги проектов). Каждый тик:
1. забирает очередь команд из БД mini по SSH (scripts/beads_remote_queue.py list);
2. выполняет их локальным bd; 'sync' — просто повод прислать свежий снимок;
3. отмечает результаты на mini (ack);
4. отправляет атомарный снимок (после команд — с принудительным перечитыванием);
5. обновляет лимиты Codex по заявке с mini (кнопка «Обновить») или если снимок
   старше 15 минут — CLI там нет, читает локальный Codex app-server;
6. по заявке `qwen-refresh.request` запускает сборщик кредитов Qwen Cloud
   (`scripts/pull_qwencloud_usage.py`), который сам присылает снимок на mini.

Пароли и ключи не нужны: используется существующий доступ SSH по ключу.
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
from bort.services import ai_providers, beads, beads_admin  # noqa: E402

_REMOTE_QUEUE = "cd ~/bort && .venv/bin/python scripts/beads_remote_queue.py"
_ATTEMPT_STAMP = Path.home() / "Library" / "Caches" / "bort" / "codex-last-attempt"
_QWEN_ATTEMPT_STAMP = Path.home() / "Library" / "Caches" / "bort" / "qwen-last-attempt"
_CODEX_MAX_AGE_SECONDS = 15 * 60
_CODEX_MIN_RETRY_SECONDS = 10 * 60
_QWEN_MIN_RETRY_SECONDS = 10 * 60
_QWEN_PULL_TIMEOUT = 120
_QWEN_PULL_SCRIPT = Path(__file__).resolve().parent / "pull_qwencloud_usage.py"
# Интерпретатор и PATH из LaunchAgent com.alluc.bort-qwencloud (там playwright).
_QWEN_DEFAULT_PYTHON = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
_QWEN_DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/Library/Frameworks/Python.framework/Versions/3.11/bin"


def _ssh_command(host: str, key_alias: str) -> list[str]:
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if key_alias:
        command.extend(["-o", f"HostKeyAlias={key_alias}"])
    return [*command, host]


def _ssh_run(host: str, key_alias: str, remote: str, *, input_bytes: bytes | None = None, timeout: int = 30):
    return subprocess.run(
        [*_ssh_command(host, key_alias), remote],
        input=input_bytes, capture_output=True, timeout=timeout, check=True, text=False,
    )


def fetch_commands(host: str, key_alias: str) -> list[dict]:
    result = _ssh_run(host, key_alias, _REMOTE_QUEUE + " list")
    try:
        data = json.loads(result.stdout.decode("utf-8") or "{}")
    except ValueError as exc:
        raise RuntimeError("mini вернул некорректную очередь команд") from exc
    commands = data.get("commands")
    if not isinstance(commands, list):
        raise RuntimeError("mini вернул некорректную очередь команд")
    return commands


def ack(host: str, key_alias: str, results: list[dict]) -> None:
    if not results:
        return
    _ssh_run(
        host, key_alias, _REMOTE_QUEUE + " ack",
        input_bytes=json.dumps(results, ensure_ascii=False).encode("utf-8"),
    )


def push_snapshot(host: str, key_alias: str, projects: list[dict]) -> None:
    document = {
        "version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "projects": projects,
    }
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 8_000_000:
        raise RuntimeError("снимок слишком велик")
    _push_file(host, key_alias, "beads-snapshot.json", payload.encode("utf-8"))


def _push_file(host: str, key_alias: str, filename: str, payload: bytes, *, timeout: int = 25) -> None:
    """Атомарно положить файл в ~/bort/data на mini с правами 0600."""
    remote = (
        "import os,pathlib,sys,tempfile; "
        "root=pathlib.Path.home()/\"bort/data\"; "
        "root.mkdir(parents=True,exist_ok=True); "
        f"fd,name=tempfile.mkstemp(prefix=\".{filename}.\",dir=root); "
        "f=os.fdopen(fd,\"wb\"); f.write(sys.stdin.buffer.read()); f.close(); "
        f"os.chmod(name,0o600); os.replace(name,root/\"{filename}\")"
    )
    _ssh_run(host, key_alias, "python3 -c " + shlex.quote(remote), input_bytes=payload, timeout=timeout)


def codex_state(host: str, key_alias: str) -> dict:
    """Одна SSH-поездка: ждёт ли mini обновления лимитов и насколько стар снимок."""
    remote = (
        "import json,pathlib,time; "
        "root=pathlib.Path.home()/\"bort/data\"; "
        "snap=root/\"codex-limits.json\"; req=root/\"codex-refresh.request\"; "
        "print(json.dumps({\"requested\": req.is_file(), "
        "\"age\": (time.time()-snap.stat().st_mtime) if snap.is_file() else None}))"
    )
    result = _ssh_run(host, key_alias, "python3 -c " + shlex.quote(remote))
    try:
        data = json.loads(result.stdout.decode("utf-8") or "{}")
    except ValueError as exc:
        raise RuntimeError("mini вернул некорректное состояние Codex") from exc
    age = data.get("age")
    return {
        "requested": bool(data.get("requested")),
        "age": float(age) if isinstance(age, (int, float)) else None,
    }


def _clear_codex_request(host: str, key_alias: str) -> None:
    remote = (
        "import pathlib; "
        "p=pathlib.Path.home()/\"bort/data/codex-refresh.request\"; "
        "p.unlink(missing_ok=True)"
    )
    _ssh_run(host, key_alias, "python3 -c " + shlex.quote(remote))


def _last_codex_attempt() -> float:
    try:
        return _ATTEMPT_STAMP.stat().st_mtime
    except OSError:
        return 0.0


def _mark_codex_attempt() -> None:
    try:
        _ATTEMPT_STAMP.parent.mkdir(parents=True, exist_ok=True)
        _ATTEMPT_STAMP.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    except OSError:
        pass


def sync_codex_limits(host: str, key_alias: str) -> int:
    """Обновить лимиты Codex на mini, если попросили или снимок устарел."""
    try:
        state = codex_state(host, key_alias)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"Codex: состояние снимка не прочитано ({type(exc).__name__})", file=sys.stderr)
        return 0
    stale = state["age"] is None or state["age"] > _CODEX_MAX_AGE_SECONDS
    if not state["requested"] and not stale:
        return 0
    if not state["requested"] and time.time() - _last_codex_attempt() < _CODEX_MIN_RETRY_SECONDS:
        return 0

    _mark_codex_attempt()
    snapshot = ai_providers.codex_limits()
    if snapshot.get("status") != "available":
        time.sleep(2)
        snapshot = ai_providers.codex_limits()
    if state["requested"]:
        try:
            _clear_codex_request(host, key_alias)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"Codex: заявку снять не удалось ({type(exc).__name__})", file=sys.stderr)
    if snapshot.get("status") != "available":
        print(f"Codex: лимиты не получены ({snapshot.get('status')}); старый снимок оставлен", file=sys.stderr)
        return 0

    document = {
        "version": 1,
        "captured_at": snapshot.get("captured_at") or snapshot.get("checked_at"),
        "status": snapshot["status"],
        "message": snapshot.get("message"),
        "data": snapshot.get("data"),
    }
    payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
    try:
        _push_file(host, key_alias, "codex-limits.json", payload)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Codex: снимок не отправлен ({type(exc).__name__})", file=sys.stderr)
        return 0
    print("Лимиты Codex обновлены")
    return 1


def qwen_state(host: str, key_alias: str) -> dict:
    """Ждёт ли mini обновления кредитов Qwen Cloud (файл-заявка)."""
    remote = (
        "import json,pathlib; "
        "root=pathlib.Path.home()/\"bort/data\"; "
        "req=root/\"qwen-refresh.request\"; "
        "print(json.dumps({\"requested\": req.is_file()}))"
    )
    result = _ssh_run(host, key_alias, "python3 -c " + shlex.quote(remote))
    try:
        data = json.loads(result.stdout.decode("utf-8") or "{}")
    except ValueError as exc:
        raise RuntimeError("mini вернул некорректное состояние Qwen") from exc
    return {"requested": bool(data.get("requested"))}


def _clear_qwen_request(host: str, key_alias: str) -> None:
    remote = (
        "import pathlib; "
        "p=pathlib.Path.home()/\"bort/data/qwen-refresh.request\"; "
        "p.unlink(missing_ok=True)"
    )
    _ssh_run(host, key_alias, "python3 -c " + shlex.quote(remote))


def _last_qwen_attempt() -> float:
    try:
        return _QWEN_ATTEMPT_STAMP.stat().st_mtime
    except OSError:
        return 0.0


def _mark_qwen_attempt() -> None:
    try:
        _QWEN_ATTEMPT_STAMP.parent.mkdir(parents=True, exist_ok=True)
        _QWEN_ATTEMPT_STAMP.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    except OSError:
        pass


def _qwen_pull_command() -> list[str]:
    """Команда сборщика: интерпретатор из LaunchAgent, переопределяемый через env."""
    override = os.environ.get("BORT_QWEN_PULL_CMD", "").strip()
    if override:
        return shlex.split(override)
    python = os.environ.get("BORT_QWEN_PYTHON", "").strip() or _QWEN_DEFAULT_PYTHON
    return [python, str(_QWEN_PULL_SCRIPT)]


def _qwen_pull_env() -> dict:
    """Окружение сборщика: PATH и интерпретатор как в LaunchAgent Qwen."""
    env = dict(os.environ)
    env["PATH"] = os.environ.get("BORT_QWEN_PULL_PATH", "").strip() or _QWEN_DEFAULT_PATH
    return env


def sync_qwen_usage(host: str, key_alias: str) -> int:
    """Запустить сборщик кредитов Qwen Cloud, если на mini есть заявка.

    После попытки (успеха или ошибки) заявка снимается, чтобы не крутиться
    вхолостую; повтор не чаще ~10 минут. Ошибки Qwen не влияют на Beads/Codex.
    """
    try:
        state = qwen_state(host, key_alias)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"Qwen: состояние заявки не прочитано ({type(exc).__name__})", file=sys.stderr)
        return 0
    if not state["requested"]:
        return 0
    if time.time() - _last_qwen_attempt() < _QWEN_MIN_RETRY_SECONDS:
        print("Qwen: заявка есть, но недавняя попытка ещё свежа — пропуск", file=sys.stderr)
        return 0

    _mark_qwen_attempt()
    try:
        result = subprocess.run(
            _qwen_pull_command(), capture_output=True, text=True, timeout=_QWEN_PULL_TIMEOUT,
            env=_qwen_pull_env(),
        )
        if result.returncode != 0:
            print(f"Qwen: сборщик завершился с кодом {result.returncode}", file=sys.stderr)
        else:
            print("Qwen: снимок кредитов отправлен")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Qwen: сборщик не запустился ({type(exc).__name__})", file=sys.stderr)

    try:
        _clear_qwen_request(host, key_alias)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Qwen: заявку снять не удалось ({type(exc).__name__})", file=sys.stderr)
    return 1


def main() -> int:
    host = os.environ.get("BORT_BEADS_SYNC_HOST", "").strip()
    if not host or host.startswith("-") or any(ch.isspace() for ch in host):
        print("BORT_BEADS_SYNC_HOST не настроен", file=sys.stderr)
        return 1
    key_alias = os.environ.get("BORT_BEADS_SYNC_HOST_KEY_ALIAS", "").strip()
    if key_alias and (key_alias.startswith("-") or any(ch.isspace() for ch in key_alias)):
        print("Некорректный SSH HostKeyAlias", file=sys.stderr)
        return 1
    if beads.is_snapshot_mode():
        print("Отправка должна выполняться на машине с локальным bd", file=sys.stderr)
        return 1

    try:
        commands = fetch_commands(host, key_alias)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"Не удалось прочитать очередь команд ({type(exc).__name__})", file=sys.stderr)
        return 1

    if commands:
        dirs = beads.discover_projects()
        binary = beads.bd_binary()
        results = beads_admin.execute_commands(commands, dirs, binary)
        try:
            ack(host, key_alias, results)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            # Команды уже выполнены; не отметили — агент выполнит их повторно.
            # Для безопасности повтор безопасен только для 'sync' и update; create
            # мог создать дубль, поэтому явно сообщаем в лог.
            print(f"Не удалось отметить команды на mini ({type(exc).__name__})", file=sys.stderr)
            return 1
        done = sum(1 for item in results if item["status"] == "done")
        print(f"Команды: выполнено {done}, ошибок {len(results) - done}")

    try:
        projects = beads.snapshots(refresh=bool(commands))
    except Exception as exc:  # bd/discovery сбой — снимок не портим
        print(f"Не удалось прочитать проекты ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not projects:
        print("Нет читаемых проектов; предыдущий снимок на mini сохранён", file=sys.stderr)
        return 1

    try:
        push_snapshot(host, key_alias, projects)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"Не удалось отправить снимок ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"Снимок отправлен: {len(projects)} проектов")

    sync_codex_limits(host, key_alias)
    try:
        sync_qwen_usage(host, key_alias)
    except Exception as exc:  # Qwen не должен ломать обработку Beads/Codex
        print(f"Qwen: неожиданный сбой ({type(exc).__name__})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
