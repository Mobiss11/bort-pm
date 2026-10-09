"""Локальный read-only обзор Beads. Источник истины — каталоги проектов, не БД Борта."""

import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

_CACHE_SECONDS = 120
_cache: dict[Path, tuple[float, dict]] = {}
_refresh_lock = threading.Lock()


def _snapshot_path() -> Path | None:
    configured = os.environ.get("BORT_BEADS_SNAPSHOT")
    path = Path(configured).expanduser() if configured else Path.home() / "bort/data/beads-snapshot.json"
    return path if configured or path.is_file() else None


def _snapshot_data() -> tuple[list[dict], dict]:
    path = _snapshot_path()
    if path is None:
        return [], {"mode": "local", "synced_at": None, "stale": False, "error": False}
    try:
        if path.stat().st_size > 8_000_000:
            raise ValueError("too large")
        data = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(data["generated_at"])
        if data.get("version") != 1 or stamp.tzinfo is None or not isinstance(data["projects"], list):
            raise ValueError("invalid snapshot")
        projects = data["projects"]
        if len(projects) > 200 or any(
            not isinstance(item, dict) or not isinstance(item.get("name"), str)
            or not isinstance(item.get("issues"), list) or not isinstance(item.get("counts"), dict)
            or not isinstance(item.get("ready"), int) or item.get("error")
            for item in projects
        ):
            raise ValueError("invalid projects")
        age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
        return projects, {
            "mode": "snapshot", "synced_at": stamp.astimezone().strftime("%d.%m.%Y %H:%M"),
            "stale": age > 600 or age < -120, "error": False,
        }
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
        return [], {"mode": "snapshot", "synced_at": None, "stale": True, "error": True}


def snapshot_info() -> dict:
    """Индикатор источника для интерфейса; не запускает bd."""
    return _snapshot_data()[1]


def is_snapshot_mode() -> bool:
    """True на mini: задачи читаются из снимка, bd рядом нет."""
    return _snapshot_path() is not None


def bd_binary() -> str | None:
    return os.environ.get("BORT_BEADS_BIN") or shutil.which("bd")


def discover_projects() -> dict[str, Path]:
    """Только проекты на глубине до двух каталогов от заданных корней."""
    roots = os.environ.get("BORT_BEADS_ROOTS", str(Path.home() / "Documents"))
    projects: dict[str, Path] = {}
    for entry in roots.split(os.pathsep):
        if not entry.strip():
            continue
        root = Path(entry).expanduser()
        if not root.is_dir():
            continue
        # Одна вложенная папка нужна, например, для Documents/сwork/проект.
        # .git, node_modules и скрытые каталоги никогда не обходим.
        queue = [(root, 0)]
        while queue:
            directory, depth = queue.pop(0)
            try:
                with os.scandir(directory) as children:
                    entries = sorted(children, key=lambda item: item.name.casefold())
            except OSError:
                continue
            for child in entries:
                if child.name.startswith(".") or child.name in {"node_modules", ".venv"}:
                    continue
                if child.is_symlink() or not child.is_dir(follow_symlinks=False):
                    continue
                path = Path(child.path)
                marker = path / ".beads"
                if not marker.is_symlink() and (marker / "config.yaml").is_file():
                    projects.setdefault(child.name, path)
                elif depth == 0:
                    queue.append((path, depth + 1))
    return dict(sorted(projects.items(), key=lambda item: item[0].casefold()))


def _read_list(directory: Path, *args: str) -> list[dict]:
    binary = bd_binary()
    if not binary:
        raise RuntimeError("Команда bd не найдена. Укажите BORT_BEADS_BIN.")
    try:
        result = subprocess.run(
            [binary, "-C", str(directory), "--readonly", *args, "--json", "--limit", "0"],
            capture_output=True, text=True, timeout=12, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Beads не ответил вовремя или недоступен.") from exc
    if result.returncode != 0:
        if "no beads database found" in result.stderr.lower():
            raise RuntimeError("Настройки Beads есть, но база задач не найдена в этом проекте.")
        raise RuntimeError("Не удалось прочитать задачи Beads в этом проекте.")
    try:
        items = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Beads вернул некорректные данные.") from exc
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RuntimeError("Beads вернул некорректный список задач.")
    return items


def _fetch(name: str, directory: Path) -> dict:
    try:
        raw = _read_list(directory, "list", "--all")
        ready = _read_list(directory, "ready")
        ready_ids = {str(item.get("id", "")) for item in ready}
        issues = []
        counts = {"open": 0, "in_progress": 0, "blocked": 0, "deferred": 0, "closed": 0}
        for item in raw:
            status = str(item.get("status", "open"))
            if status in counts:
                counts[status] += 1
            priority = item.get("priority", 2)
            priority = priority if type(priority) is int and 0 <= priority <= 4 else 2
            issues.append({
                "id": str(item.get("id", "")), "title": str(item.get("title", "")),
                "status": status, "priority": priority,
                "type": str(item.get("issue_type", "task")),
                "ready": str(item.get("id", "")) in ready_ids,
                "updated": str(item.get("updated_at", ""))[:10],
            })
        order = {"in_progress": 0, "open": 1, "blocked": 2, "deferred": 3, "closed": 4}
        issues.sort(key=lambda item: (order.get(item["status"], 5), item["priority"], item["id"]))
        return {
            "name": name, "issues": issues, "counts": counts,
            "ready": sum(item["ready"] for item in issues),
            "checked_at": datetime.now().astimezone().strftime("%d.%m.%Y %H:%M"), "error": None,
        }
    except RuntimeError as exc:
        return {"name": name, "issues": [], "counts": {}, "ready": 0, "checked_at": None, "error": str(exc)}


def snapshots(*, refresh: bool = False) -> list[dict]:
    """Кеш в памяти; не более четырёх bd-процессов одновременно, даже при двух запросах."""
    if _snapshot_path() is not None:
        return _snapshot_data()[0]
    projects = discover_projects()
    with _refresh_lock:
        for path in list(_cache):
            if path not in projects.values():
                del _cache[path]
        now = time.monotonic()
        missing = [(name, path) for name, path in projects.items()
                   if refresh or path not in _cache or now - _cache[path][0] >= _CACHE_SECONDS]
        if missing:
            with ThreadPoolExecutor(max_workers=4) as pool:
                fresh = list(pool.map(lambda pair: _fetch(*pair), missing))
            for (_, path), snapshot in zip(missing, fresh):
                _cache[path] = (time.monotonic(), snapshot)
        # Ошибочные каталоги не попадают в интерфейс и не предлагаются для привязки.
        return [_cache[path][1] for path in projects.values() if not _cache[path][1]["error"]]


def filtered_issues(snapshot: dict, *, status: str = "active", priority: str = "all", kind: str = "all", q: str = "") -> list[dict]:
    issues = snapshot["issues"]
    if status == "active":
        issues = [issue for issue in issues if issue["status"] != "closed"]
    elif status != "all":
        issues = [issue for issue in issues if issue["status"] == status]
    if priority != "all":
        issues = [issue for issue in issues if issue["priority"] == int(priority)]
    if kind != "all":
        issues = [issue for issue in issues if issue["type"] == kind]
    needle = q.strip().casefold()
    if needle:
        issues = [issue for issue in issues if needle in issue["title"].casefold() or needle in issue["id"].casefold()]
    return issues
