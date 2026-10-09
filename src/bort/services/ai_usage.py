"""Журнал AI-запросов, агрегаты и кеш снимков лимитов."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config, errors
from ..models import AIUsageCreate, validate_payload
from . import projects


def record_usage(conn, data: dict) -> dict:
    payload = validate_payload(AIUsageCreate, data)
    if payload.project_id is not None:
        projects.get_project(conn, payload.project_id)
    values = payload.model_dump()
    occurred_at = values.pop("occurred_at")
    columns = list(values)
    params = [values[column] for column in columns]
    if occurred_at is not None:
        columns.append("occurred_at")
        params.append(occurred_at)
    placeholders = ", ".join("?" for _ in columns)
    try:
        cursor = conn.execute(
            f"INSERT INTO ai_usage_events ({', '.join(columns)}) VALUES ({placeholders})",
            params,
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        if payload.external_request_id:
            raise errors.Conflict(
                "AI-запрос с таким external_request_id уже записан",
                details={"provider": payload.provider, "external_request_id": payload.external_request_id},
            ) from exc
        raise
    return get_usage_event(conn, cursor.lastrowid)


def get_usage_event(conn, event_id: int) -> dict:
    row = conn.execute(
        "SELECT e.*, p.name AS project_name FROM ai_usage_events e "
        "LEFT JOIN projects p ON p.id = e.project_id WHERE e.id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise errors.NotFound(f"AI-событие {event_id} не найдено", details={"event_id": event_id})
    return dict(row)


def list_usage(conn, *, provider: str | None = None, limit: int = 50) -> dict:
    limit = max(1, min(int(limit), 200))
    where = "WHERE e.provider = ?" if provider else ""
    params = [provider, limit] if provider else [limit]
    rows = conn.execute(
        "SELECT e.*, p.name AS project_name FROM ai_usage_events e "
        "LEFT JOIN projects p ON p.id = e.project_id "
        f"{where} ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?",
        params,
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


def _summary_since(conn, since: str) -> dict:
    row = conn.execute(
        """
        SELECT count(*) AS requests,
               coalesce(sum(input_tokens), 0) AS input_tokens,
               coalesce(sum(output_tokens), 0) AS output_tokens,
               coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens,
               coalesce(sum(cache_write_tokens), 0) AS cache_write_tokens,
               coalesce(sum(cost_microusd), 0) AS cost_microusd,
               coalesce(sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END), 0) AS errors
          FROM ai_usage_events
         WHERE occurred_at >= ?
        """,
        (since,),
    ).fetchone()
    return dict(row)


def usage_summary(conn) -> dict:
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo(config.tz_name()))
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = local_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    week_start = (now - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    month_start = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    return {
        "today": _summary_since(conn, today_start),
        "seven_days": _summary_since(conn, week_start),
        "thirty_days": _summary_since(conn, month_start),
    }


def save_snapshot(conn, snapshot: dict) -> dict:
    provider = snapshot["provider"]
    existing = conn.execute(
        "SELECT payload_json, captured_at FROM ai_limit_snapshots WHERE provider = ?", (provider,)
    ).fetchone()
    data = snapshot.get("data")
    payload_json = json.dumps(data, ensure_ascii=False) if data is not None else None
    captured_at = snapshot.get("captured_at")
    if data is None and existing is not None and snapshot["status"] != "available":
        # Неудачное обновление (нет CLI, вход истёк, сеть) не стирает прежние цифры:
        # карточка покажет их с пометкой текущего состояния.
        payload_json = existing["payload_json"]
        captured_at = existing["captured_at"]
    conn.execute(
        """
        INSERT INTO ai_limit_snapshots
            (provider, status, source, payload_json, captured_at, checked_at, message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            status = excluded.status,
            source = excluded.source,
            payload_json = excluded.payload_json,
            captured_at = excluded.captured_at,
            checked_at = excluded.checked_at,
            message = excluded.message
        """,
        (
            provider,
            snapshot["status"],
            snapshot["source"],
            payload_json,
            captured_at,
            snapshot["checked_at"],
            snapshot.get("message"),
        ),
    )
    conn.commit()
    return get_snapshot(conn, provider)


def get_snapshot(conn, provider: str) -> dict:
    row = conn.execute("SELECT * FROM ai_limit_snapshots WHERE provider = ?", (provider,)).fetchone()
    if row is None:
        return {
            "provider": provider,
            "status": "not_checked",
            "source": "—",
            "data": None,
            "captured_at": None,
            "checked_at": None,
            "message": "Ещё не обновлялось",
        }
    result = dict(row)
    payload = result.pop("payload_json")
    result["data"] = json.loads(payload) if payload else None
    return result


def refresh_codex(conn, *, request_refresh: bool = False) -> dict:
    from .ai_providers import codex_limits, request_codex_refresh

    snapshot = codex_limits()
    if request_refresh and "MacBook" in str(snapshot.get("source", "")):
        # Данные приходят снимком с MacBook: просим агента перечитать CLI.
        # Автополлинг карточки (GET) заявок не создаёт — иначе агент дёргал бы CLI каждую минуту.
        request_codex_refresh()
    return save_snapshot(conn, snapshot)


def refresh_deepseek(conn) -> dict:
    from .ai_providers import deepseek_balance

    return save_snapshot(conn, deepseek_balance())


def refresh_qwen(conn, *, request_refresh: bool = False) -> dict:
    from .ai_providers import qwen_plan, request_qwen_refresh

    snapshot = qwen_plan()
    if request_refresh and "MacBook" in str(snapshot.get("source", "")):
        # Данные приходят снимком с MacBook: просим агента перечитать консоль Qwen.
        # Автополлинг карточки (GET) заявок не создаёт — иначе сборщик дёргался бы каждую минуту.
        request_qwen_refresh()
    return save_snapshot(conn, snapshot)


def refresh_zai(conn) -> dict:
    from .ai_providers import zai_limits

    return save_snapshot(conn, zai_limits())


def refresh_claude(conn) -> dict:
    from .ai_providers import claude_limits

    return save_snapshot(conn, claude_limits())


def refresh_openrouter(conn) -> dict:
    from .ai_providers import openrouter_balance

    return save_snapshot(conn, openrouter_balance())


REFRESHERS = {
    "claude": refresh_claude,
    "deepseek": refresh_deepseek,
    "codex": refresh_codex,
    "qwen": refresh_qwen,
    "zai": refresh_zai,
    "openrouter": refresh_openrouter,
}

PROVIDERS = tuple(REFRESHERS)


def refresh_all(conn, *, request_remote: bool = True) -> dict:
    """Обновить все провайдеры, не давая сбою одного остановить остальные.

    Для codex и qwen при ``request_remote`` ставится заявка MacBook-агенту
    (там CLI/браузер, которых нет на mini). Сбойный провайдер сохраняется
    со статусом ошибки, но прежние цифры снимка не теряются (save_snapshot).
    """
    providers: dict[str, dict] = {}
    remote_requested: list[str] = []
    for name, refresher in REFRESHERS.items():
        try:
            if name in ("codex", "qwen"):
                snapshot = refresher(conn, request_refresh=request_remote)
            else:
                snapshot = refresher(conn)
        except Exception as exc:  # один сбойный провайдер не должен ломать остальные
            snapshot = _save_refresh_error(conn, name, exc)
        providers[name] = snapshot
        if request_remote and "MacBook" in str(snapshot.get("source", "")):
            remote_requested.append(name)
    return {"providers": providers, "remote_requested": remote_requested}


def _save_refresh_error(conn, provider: str, exc: Exception) -> dict:
    from .. import errors as _errors

    previous = get_snapshot(conn, provider)
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    message = exc.message if isinstance(exc, _errors.BortError) else str(exc)
    return save_snapshot(
        conn,
        {
            "provider": provider,
            "status": "error",
            "source": previous.get("source") or "—",
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": f"Обновление не удалось: {message or 'неизвестная ошибка'}",
        },
    )


def dashboard(conn) -> dict:
    return {
        "usage": usage_summary(conn),
        "providers": {name: get_snapshot(conn, name) for name in PROVIDERS},
        "recent": list_usage(conn, limit=50)["items"],
    }
