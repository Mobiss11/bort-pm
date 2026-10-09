"""Команды к Beads из интерфейса и скрытые проекты.

Запись задач возможна только через bd на машине, где лежат проекты (MacBook).
Поэтому здесь только очередь: локально команда выполняется сразу, на mini —
ждёт sync-агента, который заберёт её по SSH, выполнит bd и вернёт снимок.
"""

import json
import subprocess

from .. import errors

COMMAND_KINDS = ("create", "update", "close", "reopen", "sync")
SYNC_PROJECT = "*"  # команда синхронизации без выбранного проекта
# Типы из bd types (встроенные): интерфейс предлагает ровно этот набор.
TASK_TYPES = ("task", "bug", "feature", "chore", "epic", "decision", "spike", "story", "milestone")
# Закрытие/возврат — отдельными командами bd; список статусов для update.
OPEN_STATUSES = ("open", "in_progress", "blocked", "deferred")

_BD_TIMEOUT = 25


# --- Скрытые проекты (только список Борта, файлы на Mac не трогаются) ---


def hidden_names(conn) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM beads_hidden_projects")}


def hide_project(conn, name: str) -> None:
    name = _required_text(name, field="Проект", limit=200)
    conn.execute("INSERT OR IGNORE INTO beads_hidden_projects (name) VALUES (?)", (name,))
    conn.commit()


def unhide_project(conn, name: str) -> None:
    name = _required_text(name, field="Проект", limit=200)
    conn.execute("DELETE FROM beads_hidden_projects WHERE name = ?", (name,))
    conn.commit()


# --- Нормализация команд ---


def _required_text(value, *, field: str, limit: int, optional: bool = False):
    text = str(value or "").strip()
    if not text:
        if optional:
            return None
        raise errors.ValidationError(f"Поле «{field}» обязательно")
    if len(text) > limit:
        raise errors.ValidationError(f"Поле «{field}» длиннее {limit} символов")
    return text


def _priority(value, *, field: str = "Приоритет") -> int:
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise errors.ValidationError(f"{field}: нужно число от 0 до 4") from exc
    if not 0 <= level <= 4:
        raise errors.ValidationError(f"{field}: нужно число от 0 до 4")
    return level


def _normalize(kind: str, payload: dict) -> dict:
    payload = dict(payload or {})
    if kind == "create":
        issue_type = str(payload.get("type") or "task").strip().lower()
        if issue_type not in TASK_TYPES:
            raise errors.ValidationError("Неизвестный тип задачи", details={"allowed": list(TASK_TYPES)})
        return {
            "title": _required_text(payload.get("title"), field="Название", limit=200),
            "type": issue_type,
            "priority": _priority(payload.get("priority", 2)),
            "description": _required_text(
                payload.get("description"), field="Описание", limit=4000, optional=True
            ),
        }
    if kind == "update":
        normalized = {"issue": _required_text(payload.get("issue"), field="Задача", limit=100)}
        status = str(payload.get("status") or "").strip()
        if status:
            if status not in OPEN_STATUSES:
                raise errors.ValidationError(
                    "Неизвестный статус задачи", details={"allowed": list(OPEN_STATUSES)}
                )
            normalized["status"] = status
        if payload.get("priority") not in (None, ""):
            normalized["priority"] = _priority(payload.get("priority"))
        if "status" not in normalized and "priority" not in normalized:
            raise errors.ValidationError("Не выбрано ни одного изменения задачи")
        return normalized
    if kind == "close":
        return {
            "issue": _required_text(payload.get("issue"), field="Задача", limit=100),
            "reason": _required_text(
                payload.get("reason"), field="Причина", limit=300, optional=True
            ),
        }
    if kind == "reopen":
        return {"issue": _required_text(payload.get("issue"), field="Задача", limit=100)}
    if kind == "sync":
        return {}
    raise errors.ValidationError("Неизвестная команда", details={"allowed": list(COMMAND_KINDS)})


# --- Очередь ---


def _row(row) -> dict:
    item = dict(row)
    try:
        item["payload"] = json.loads(item["payload"])
    except (TypeError, ValueError):
        item["payload"] = {}
    return item


def get_command(conn, command_id: int) -> dict:
    row = conn.execute("SELECT * FROM beads_commands WHERE id = ?", (command_id,)).fetchone()
    if row is None:
        raise errors.NotFound(f"Команда {command_id} не найдена", details={"command_id": command_id})
    return _row(row)


def enqueue(conn, *, project: str, kind: str, payload: dict, available: set[str] | None = None) -> dict:
    if kind not in COMMAND_KINDS:
        raise errors.ValidationError("Неизвестная команда", details={"allowed": list(COMMAND_KINDS)})
    if kind == "sync":
        project = str(project or "").strip() or SYNC_PROJECT
    else:
        project = _required_text(project, field="Проект", limit=200)
        if available is not None and project not in available:
            raise errors.ValidationError("Проект задач не найден среди доступных")
    normalized = _normalize(kind, payload)
    payload_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    duplicate = conn.execute(
        "SELECT id FROM beads_commands WHERE status = 'pending' AND project = ? AND kind = ? AND payload = ?",
        (project, kind, payload_json),
    ).fetchone()
    if duplicate is not None:
        raise errors.Conflict("Эта команда уже отправлена и ждёт выполнения")
    cur = conn.execute(
        "INSERT INTO beads_commands (project, kind, payload) VALUES (?, ?, ?)",
        (project, kind, payload_json),
    )
    conn.commit()
    return get_command(conn, cur.lastrowid)


def list_commands(conn, *, limit_pending: int = 50, limit_errors: int = 5) -> dict:
    pending = [
        _row(row)
        for row in conn.execute(
            "SELECT * FROM beads_commands WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit_pending,),
        )
    ]
    errors_rows = [
        _row(row)
        for row in conn.execute(
            "SELECT * FROM beads_commands WHERE status = 'error' ORDER BY id DESC LIMIT ?",
            (limit_errors,),
        )
    ]
    return {"pending": pending, "errors": errors_rows, "count": len(pending)}


def pending_for_remote(conn, *, limit: int = 100) -> list[dict]:
    """Список команд для sync-агента (без служебных полей)."""
    return [
        _row(row)
        for row in conn.execute(
            "SELECT * FROM beads_commands WHERE status = 'pending' ORDER BY id ASC LIMIT ?",
            (limit,),
        )
    ]


def mark(conn, results: list[dict]) -> int:
    """Отметить результаты выполнения: [{'id': 1, 'status': 'done'|'error', 'error': ...}]."""
    updated = 0
    for item in results or []:
        try:
            command_id = int(item["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise errors.ValidationError("Некорректный результат команды") from exc
        status = str(item.get("status", ""))
        if status not in ("done", "error"):
            raise errors.ValidationError("Статус результата: done или error")
        error = _required_text(item.get("error"), field="Ошибка", limit=500, optional=True)
        cur = conn.execute(
            "UPDATE beads_commands SET status = ?, error = ?, "
            "processed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ? AND status = 'pending'",
            (status, error, command_id),
        )
        updated += cur.rowcount
    conn.commit()
    return updated


def dismiss(conn, command_id: int) -> None:
    command = get_command(conn, command_id)
    if command["status"] == "pending":
        raise errors.ValidationError("Команда ещё ждёт выполнения — её нельзя снять")
    conn.execute("DELETE FROM beads_commands WHERE id = ?", (command_id,))
    conn.commit()


# --- Выполнение через bd (там, где есть bd) ---


def build_args(kind: str, payload: dict) -> list[str] | None:
    """Аргументы bd без пути проекта; None — команде не нужен bd (sync)."""
    if kind == "create":
        args = ["create", "--title", payload["title"]]
        if payload.get("type"):
            args += ["--type", payload["type"]]
        args += ["-p", str(payload.get("priority", 2))]
        if payload.get("description"):
            args += ["-d", payload["description"]]
        return args
    if kind == "update":
        args = ["update", payload["issue"]]
        if payload.get("status"):
            args += ["--status", payload["status"]]
        if payload.get("priority") is not None:
            args += ["-p", str(payload["priority"])]
        return args
    if kind == "close":
        args = ["close", payload["issue"]]
        if payload.get("reason"):
            args += ["--reason", payload["reason"]]
        return args
    if kind == "reopen":
        return ["reopen", payload["issue"]]
    if kind == "sync":
        return None
    raise errors.ValidationError("Неизвестная команда")


def run_bd(binary: str, directory, args: list[str]) -> str | None:
    """Выполнить запись bd. None — успех, иначе короткий текст ошибки."""
    try:
        result = subprocess.run(
            [binary, "-C", str(directory), *args],
            capture_output=True, text=True, timeout=_BD_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "bd не ответил вовремя или недоступен"
    if result.returncode != 0:
        lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
        return (lines[0] if lines else "bd завершился с ошибкой")[:300]
    return None


def execute_commands(commands: list[dict], dirs: dict, binary: str | None) -> list[dict]:
    """Выполнить команды над каталогами проектов; вернуть результаты для mark()."""
    results = []
    for command in commands:
        kind = str(command.get("kind", ""))
        payload = command.get("payload") or {}
        try:
            args = build_args(kind, payload)
        except errors.BortError as exc:
            results.append({"id": command["id"], "status": "error", "error": exc.message})
            continue
        if args is None:  # sync: только повод прислать свежий снимок
            results.append({"id": command["id"], "status": "done", "error": None})
            continue
        directory = dirs.get(str(command.get("project", "")))
        if not binary:
            results.append({"id": command["id"], "status": "error", "error": "bd не найден на машине"})
            continue
        if directory is None:
            results.append({"id": command["id"], "status": "error", "error": "Проект не найден на машине с bd"})
            continue
        error = run_bd(binary, directory, args)
        results.append({"id": command["id"], "status": "error" if error else "done", "error": error})
    return results


def process_pending_locally(conn) -> int:
    """Локальный режим (bd есть): выполнить очередь сразу, не дожидаясь агента."""
    from . import beads as beads_svc

    pending = pending_for_remote(conn)
    if not pending:
        return 0
    binary = beads_svc.bd_binary()
    dirs = beads_svc.discover_projects()
    mark(conn, execute_commands(pending, dirs, binary))
    return len(pending)
