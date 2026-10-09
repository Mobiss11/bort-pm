"""Адаптеры источников AI-лимитов. Секреты никогда не возвращаются и не пишутся в БД."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _get_json(url: str, *, headers: dict, timeout: float = 6):
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 — URL фиксирован конфигурацией
        return json.loads(response.read().decode("utf-8"))


def _percent(value: float) -> int | float:
    """Проценты без хвоста «.0»: в интерфейсе сравнение чисел глазами важнее точности."""
    rounded = round(float(value), 1)
    return int(rounded) if rounded == int(rounded) else rounded


def deepseek_balance() -> dict:
    """Читает официальный DeepSeek GET /user/balance."""
    checked_at = _iso()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return {
            "provider": "deepseek",
            "status": "not_configured",
            "source": "DeepSeek API /user/balance",
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Добавьте DEEPSEEK_API_KEY в окружение Борта",
        }

    url = os.environ.get("BORT_DEEPSEEK_BALANCE_URL", "https://api.deepseek.com/user/balance")
    request = Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 — URL фиксирован конфигурацией
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict) or not isinstance(body.get("is_available"), bool) or not isinstance(body.get("balance_infos"), list):
            raise ValueError("unexpected DeepSeek response")
        balances = []
        for item in body.get("balance_infos", []):
            if not isinstance(item, dict):
                raise ValueError("unexpected DeepSeek balance")
            balances.append(
                {
                    "currency": str(item.get("currency", "")),
                    "total_balance": str(item.get("total_balance", "0")),
                    "granted_balance": str(item.get("granted_balance", "0")),
                    "topped_up_balance": str(item.get("topped_up_balance", "0")),
                }
            )
        captured_at = _iso()
        return {
            "provider": "deepseek",
            "status": "available",
            "source": "DeepSeek API /user/balance",
            "checked_at": captured_at,
            "captured_at": captured_at,
            "data": {"is_available": bool(body.get("is_available")), "balances": balances},
            "message": None,
        }
    except HTTPError as exc:
        message = f"DeepSeek вернул HTTP {exc.code}"
    except (URLError, TimeoutError):
        message = "DeepSeek недоступен или не ответил вовремя"
    except (ValueError, TypeError, KeyError):
        message = "DeepSeek вернул неожиданный формат ответа"
    return {
        "provider": "deepseek",
        "status": "error",
        "source": "DeepSeek API /user/balance",
        "checked_at": checked_at,
        "captured_at": None,
        "data": None,
        "message": message,
    }


def _codex_rpc(proc, method: str, ident: int, *, params: dict | None = None) -> dict:
    message = {"method": method, "id": ident}
    if params is not None:
        message["params"] = params
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], max(0, deadline - time.monotonic()))
        if not ready:
            break
        line = proc.stdout.readline()
        if not line:
            break
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if result.get("id") == ident:
            if "error" in result:
                raise ValueError("Codex app-server returned an error")
            return result.get("result", {})
    raise TimeoutError("Codex app-server did not respond")


def _codex_window(value: dict | None) -> dict | None:
    if not isinstance(value, dict) or value.get("usedPercent") is None:
        return None
    used = max(0.0, min(100.0, float(value["usedPercent"])))
    reset = value.get("resetsAt")
    return {
        "remaining_percent": round(100 - used, 1),
        "window_minutes": value.get("windowDurationMins"),
        "resets_at": _iso(datetime.fromtimestamp(reset, tz=timezone.utc)) if isinstance(reset, (int, float)) else None,
    }


def request_codex_refresh() -> bool:
    """Попросить MacBook-агента обновить лимиты Codex (файл-заявка на mini).

    Агент на MacBook забирает заявку в течение ~30 секунд: читает CLI и присылает
    свежий снимок. Прямой вызов CLI здесь невозможен — на mini его нет.
    """
    path = Path(
        os.environ.get("BORT_CODEX_REQUEST_FILE") or Path.home() / "bort" / "data" / "codex-refresh.request"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_iso(), encoding="utf-8")
        return True
    except OSError:
        return False


def request_qwen_refresh() -> bool:
    """Попросить MacBook-агента обновить кредиты Qwen Cloud (файл-заявка на mini).

    Агент на MacBook забирает заявку в течение ~30 секунд: запускает
    `scripts/pull_qwencloud_usage.py`, который читает консоль и присылает свежий
    снимок. Прямой сбор здесь невозможен — на mini нет залогиненного браузера.
    """
    path = Path(
        os.environ.get("BORT_QWEN_REQUEST_FILE") or Path.home() / "bort" / "data" / "qwen-refresh.request"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_iso(), encoding="utf-8")
        return True
    except OSError:
        return False


def _codex_snapshot() -> dict | None:
    """Снимок лимитов с MacBook (как у Qwen): есть файл — CLI не нужен."""
    path = Path(
        os.environ.get("BORT_CODEX_LIMITS_FILE") or Path.home() / "bort" / "data" / "codex-limits.json"
    )
    if not path.is_file():
        return None
    checked_at = _iso()
    source = "Codex CLI с MacBook · account/rateLimits/read"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("captured_at"), str):
            raise ValueError("invalid snapshot")
    except (OSError, ValueError, TypeError):
        return {
            "provider": "codex",
            "status": "error",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Снимок Codex повреждён — обновите его на MacBook",
        }
    status = str(data.get("status") or "error")
    if status not in ("available", "unavailable", "not_configured", "error", "auth_required"):
        status = "error"
    captured_at = data["captured_at"]
    message = data.get("message")
    try:
        age_hours = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        ).total_seconds() / 3600
        if age_hours > 3:
            message = f"Снимок старше {int(age_hours)} ч — обновите его на MacBook"
    except ValueError:
        message = "Не удалось разобрать время снимка"
    return {
        "provider": "codex",
        "status": status,
        "source": source,
        "checked_at": checked_at,
        "captured_at": captured_at if status == "available" else None,
        "data": data.get("data") if status == "available" else None,
        "message": message,
    }


def codex_limits() -> dict:
    """Официальный Codex app-server: read-only account + rateLimits (не OpenCode API).

    На mini Codex CLI нет — там используется снимок, который кладёт MacBook
    (scripts/pull_codex_limits.py); локально команда читает CLI напрямую.
    """
    snapshot = _codex_snapshot()
    if snapshot is not None:
        return snapshot
    checked_at = _iso()
    source = "Локальная учётная запись Codex CLI · account/rateLimits/read"
    binary = shutil.which("codex")
    if binary is None:
        return {
            "provider": "codex", "status": "unavailable", "source": source,
            "checked_at": checked_at, "captured_at": None, "data": None,
            "message": "Codex CLI не найден; лимиты подписки OpenCode напрямую не предоставляет",
        }
    proc = None
    try:
        proc = subprocess.Popen(
            [binary, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        _codex_rpc(proc, "initialize", 1, params={
            "clientInfo": {"name": "bort", "title": "Борт", "version": "0.1.0"}
        })
        proc.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        proc.stdin.flush()
        account = _codex_rpc(proc, "account/read", 2, params={"refreshToken": False}).get("account")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise ValueError("Локальный Codex CLI не авторизован через подписку ChatGPT")
        result = _codex_rpc(proc, "account/rateLimits/read", 3)
        buckets = result.get("rateLimitsByLimitId") or {}
        if not buckets and result.get("rateLimits"):
            buckets = {"codex": result["rateLimits"]}
        output = []
        for bucket_id, bucket in buckets.items():
            if not isinstance(bucket, dict):
                continue
            output.append({
                "id": bucket_id,
                "name": bucket.get("limitName") or bucket_id,
                "primary": _codex_window(bucket.get("primary")),
                "secondary": _codex_window(bucket.get("secondary")),
            })
        if not output:
            raise ValueError("Codex не вернул окна лимитов")
        return {
            "provider": "codex", "status": "available", "source": source,
            "checked_at": _iso(), "captured_at": _iso(),
            "data": {"account_email": account.get("email"), "plan_type": account.get("planType"), "buckets": output},
            "message": "Сверьте эту учётную запись с авторизацией GPT в OpenCode",
        }
    except (OSError, ValueError, TimeoutError, TypeError, OverflowError):
        return {
            "provider": "codex", "status": "unavailable", "source": source,
            "checked_at": checked_at, "captured_at": None, "data": None,
            "message": "Нет доступных лимитов Codex CLI. Проверьте его вход в ChatGPT или версию CLI",
        }
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()


# --- Z.AI: остаток Coding Plan ---

_ZAI_UNIT_MINUTES = {1: 1440, 3: 60, 5: 1, 6: 10080}


def _zai_window_minutes(unit: int, number: int) -> int | None:
    multiplier = _ZAI_UNIT_MINUTES.get(unit)
    if not multiplier or number <= 0:
        return None
    return number * multiplier


def _zai_label(kind: str, unit: int, number: int) -> str:
    if kind == "TIME_LIMIT":
        return "MCP"
    minutes = _zai_window_minutes(unit, number)
    if minutes == 300:
        return "5 часов"
    if minutes == 10080:
        return "7 дней"
    if minutes:
        if minutes % 1440 == 0:
            return f"{minutes // 1440} дн."
        if minutes % 60 == 0:
            return f"{minutes // 60} ч"
        return f"{minutes} мин"
    return "окно"


def _zai_row(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in ("TOKENS_LIMIT", "TIME_LIMIT", "CREDIT_LIMIT"):
        return None
    unit, number = raw.get("unit"), raw.get("number")
    percent = raw.get("percentage")
    if not isinstance(unit, int) or not isinstance(number, int) or not isinstance(percent, (int, float)):
        return None
    used = float(percent)
    usage = raw.get("usage")
    remaining = raw.get("remaining")
    current = raw.get("currentValue")
    credits_used = None
    credits_total = None
    if isinstance(usage, int) and usage > 0:
        used_value = None
        if isinstance(remaining, int):
            used_value = max(0, usage - remaining)
        elif isinstance(current, int):
            used_value = current
        if used_value is not None:
            used = used_value / usage * 100  # у CREDIT_LIMIT usage — это кредиты, а не проценты
            credits_used, credits_total = used_value, usage
    used = min(100.0, max(0.0, used))
    minutes = _zai_window_minutes(unit, number)
    if kind == "TIME_LIMIT" and unit == 5 and number == 1:
        minutes = 30 * 24 * 60
    reset = raw.get("nextResetTime")
    resets_at = None
    if isinstance(reset, int) and reset > 0:
        # Пятичасовой сброс не может быть дальше пяти часов: защита от чужой TZ.
        five_hour_delay = (5 * 3600 + 60) * 1000
        if not (kind != "TIME_LIMIT" and minutes == 300 and reset > time.time() * 1000 + five_hour_delay):
            resets_at = _iso(datetime.fromtimestamp(reset / 1000, tz=timezone.utc))
    return {
        "kind": kind,
        "label": _zai_label(kind, unit, number),
        "used_percent": _percent(used),
        "remaining_percent": _percent(100 - used),
        "window_minutes": minutes,
        "resets_at": resets_at,
        "credits_used": credits_used,
        "credits_total": credits_total,
    }


def zai_limits() -> dict:
    """Остаток Coding Plan Z.AI: monitor-эндпоинт панели (не публичный API)."""
    checked_at = _iso()
    source = "Z.AI · /api/monitor/usage/quota/limit"
    api_key = _env("ZAI_API_KEY", "Z_AI_API_KEY")
    if not api_key:
        return {
            "provider": "zai",
            "status": "not_configured",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Добавьте ZAI_API_KEY (ключ Z.AI Coding Plan) в окружение Борта",
        }
    url = os.environ.get("BORT_ZAI_QUOTA_URL", "https://api.z.ai/api/monitor/usage/quota/limit")
    try:
        body = _get_json(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
        if isinstance(body, dict) and body.get("success") is not True:
            code = body.get("code")
            reason = str(body.get("msg") or body.get("message") or f"код {code}")
            if code == 401:
                return {
                    "provider": "zai",
                    "status": "error",
                    "source": source,
                    "checked_at": checked_at,
                    "captured_at": None,
                    "data": None,
                    "message": f"Z.AI не принял ключ: {reason}",
                }
            return {
                "provider": "zai",
                "status": "error",
                "source": source,
                "checked_at": checked_at,
                "captured_at": None,
                "data": None,
                "message": f"Z.AI: {reason}",
            }
        data = body.get("data") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("success") is not True
            or body.get("code") != 200
            or not isinstance(data, dict)
            or not isinstance(data.get("limits"), list)
        ):
            raise ValueError("unexpected zai response")
        windows = [row for row in (_zai_row(item) for item in data["limits"]) if row]
        if not windows:
            raise ValueError("no zai limits")
        plan = (
            data.get("planName") or data.get("plan") or data.get("plan_type")
            or data.get("packageName") or data.get("level")
        )
        captured_at = _iso()
        return {
            "provider": "zai",
            "status": "available",
            "source": source,
            "checked_at": captured_at,
            "captured_at": captured_at,
            "data": {"plan": str(plan) if plan else None, "windows": windows},
            "message": "Эндпоинт панели Z.AI, не публичный API — может меняться",
        }
    except HTTPError as exc:
        message = f"Z.AI вернул HTTP {exc.code}"
    except (URLError, TimeoutError):
        message = "Z.AI недоступен или не ответил вовремя"
    except (ValueError, TypeError, KeyError):
        message = "Z.AI вернул неожиданный формат ответа"
    return {
        "provider": "zai",
        "status": "error",
        "source": source,
        "checked_at": checked_at,
        "captured_at": None,
        "data": None,
        "message": message,
    }


# --- Qwen Cloud Token Plan: кредиты из снимка консоли ---


def qwen_plan() -> dict:
    """Кредиты Qwen Cloud Token Plan из снимка, который кладёт MacBook.

    Qwen Cloud не отдаёт остаток Credits по ключу подписки: консоль требует сессию
    браузера. Скрипт `scripts/pull_qwencloud_usage.py` на MacBook читает консоль
    (копия залогиненного профиля Chrome) и присылает снимок в `~/bort/data/`;
    здесь он только отображается. Секреты никуда не сохраняются.
    """
    checked_at = _iso()
    source = "Qwen Cloud · снимок консоли с MacBook"
    path = Path(
        os.environ.get("BORT_QWENCLOUD_FILE") or Path.home() / "bort" / "data" / "qwencloud-usage.json"
    )
    if not path.is_file():
        return {
            "provider": "qwen",
            "status": "not_configured",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Снимок ещё не получен. На MacBook выполните python3 scripts/pull_qwencloud_usage.py",
        }
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if snapshot.get("version") != 1 or not isinstance(snapshot.get("captured_at"), str):
            raise ValueError("invalid snapshot")
    except (OSError, ValueError, TypeError):
        return {
            "provider": "qwen",
            "status": "error",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Снимок Qwen Cloud повреждён — обновите его на MacBook",
        }
    status = snapshot.get("status")
    if status == "login_required":
        return {
            "provider": "qwen",
            "status": "auth_required",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Chrome на MacBook не залогинен в консоли Qwen Cloud — войдите и повторите снимок",
        }
    if status != "ok":
        return {
            "provider": "qwen",
            "status": "error",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": str(snapshot.get("message") or "Снимок Qwen Cloud не получен"),
        }

    captured_at = snapshot["captured_at"]
    plan = snapshot.get("plan") or {}
    credits = snapshot.get("credits") or {}
    total = credits.get("total")
    remaining = credits.get("remaining")
    used_percent = credits.get("used_percent")
    windows = []
    if isinstance(total, int) and isinstance(remaining, int) and total > 0:
        windows.append(
            {
                "label": "Кредиты (месяц)",
                "used_percent": _percent(used_percent if isinstance(used_percent, (int, float)) else 0),
                "remaining_percent": _percent(remaining / total * 100),
                "window_minutes": None,
                "resets_at": credits.get("reset_at"),
                "detail": f"осталось {remaining:,} из {total:,}".replace(",", " "),
            }
        )
    message = None
    try:
        age_hours = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        ).total_seconds() / 3600
        if age_hours > 3:
            message = f"Снимок старше {int(age_hours)} ч — обновите его на MacBook"
    except ValueError:
        message = "Не удалось разобрать время снимка"
    return {
        "provider": "qwen",
        "status": "available",
        "source": source,
        "checked_at": checked_at,
        "captured_at": captured_at,
        "data": {
            "plan": plan.get("spec"),
            "remaining_days": plan.get("remaining_days"),
            "console_url": "https://home.qwencloud.com/analytics/token-plan/individual",
            "windows": windows,
        },
        "message": message,
    }


# --- Claude (подписка через kraube serve): окна 5 часов / 7 дней ---

_CLAUDE_WINDOW_MINUTES = {"five_hour": 300, "seven_day": 10080}
_CLAUDE_LABELS = {"five_hour": "5 часов", "seven_day": "7 дней"}


def _claude_window(kind: str, raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percent")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    used = min(100.0, max(0.0, float(used)))
    resets_at = raw.get("resets_at")
    if isinstance(resets_at, str) and resets_at:
        try:
            datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
        except ValueError:
            resets_at = None
    else:
        resets_at = None
    resets_in = raw.get("resets_in")
    return {
        "label": _CLAUDE_LABELS.get(kind, "окно"),
        "used_percent": _percent(used),
        "remaining_percent": _percent(100 - used),
        "window_minutes": _CLAUDE_WINDOW_MINUTES.get(kind),
        "resets_at": resets_at,
        "detail": f"сброс через {resets_in}" if isinstance(resets_in, str) and resets_in else None,
    }


def claude_limits() -> dict:
    """Окна подписки Claude из локального демона kraube serve (GET /usage).

    kraube на mini держит OAuth-подписку и кэширует rate-limit окна из
    заголовков последних проксированных запросов: сам /usage платных
    обращений к Anthropic не делает. До первого запроса через подписку
    кэш пуст и /usage отвечает 404. Ключ — serve-ключ демона, не ключ
    Anthropic.
    """
    checked_at = _iso()
    source = "kraube serve · GET /usage (подписка Claude)"
    api_key = _env("KRAUBE_SERVE_KEY")
    if not api_key:
        return {
            "provider": "claude",
            "status": "not_configured",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Добавьте KRAUBE_SERVE_KEY (serve-ключ демона kraube) в ~/.config/bort/env",
        }
    url = os.environ.get("BORT_KRAUBE_USAGE_URL", "http://127.0.0.1:8787/usage")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        body = _get_json(url, headers=headers)
        if not isinstance(body, dict):
            raise ValueError("unexpected kraube response")
        windows = [row for row in (_claude_window(kind, body.get(kind)) for kind in ("five_hour", "seven_day")) if row]
        if not windows:
            raise ValueError("no claude windows")
        unified = str(body.get("status") or "")
        message = None
        if unified == "rejected":
            message = "Подписка упёрлась в лимит: Claude отклоняет запросы до сброса окна"
        elif unified == "allowed_warning":
            message = "Лимит подписки приближается — экономьте окно"
        captured_at = body.get("updated_at")
        cache_age = body.get("age")
        data = {
            "windows": windows,
            "unified_status": unified or None,
            "claim": str(body["claim"]) if isinstance(body.get("claim"), str) and body["claim"] else None,
            "cache_age": str(cache_age) if isinstance(cache_age, str) and cache_age else None,
        }
        return {
            "provider": "claude",
            "status": "available",
            "source": source,
            "checked_at": _iso(),
            "captured_at": captured_at if isinstance(captured_at, str) and captured_at else _iso(),
            "data": data,
            "message": message,
        }
    except HTTPError as exc:
        if exc.code == 404:
            message = "kraube ещё не получил лимиты: они появятся после первого запроса через подписку"
        else:
            message = f"kraube вернул HTTP {exc.code}"
    except (URLError, TimeoutError):
        message = "kraube serve недоступен или не ответил вовремя (демон должен работать на mini)"
    except (ValueError, TypeError, KeyError):
        message = "kraube вернул неожиданный формат ответа"
    return {
        "provider": "claude",
        "status": "error",
        "source": source,
        "checked_at": checked_at,
        "captured_at": None,
        "data": None,
        "message": message,
    }


# --- OpenRouter: баланс аккаунта и расход ключа ---


def _openrouter_models(activity, days: int = 7) -> list[dict]:
    """Топ моделей по расходу за N дней из /activity (нужен management-ключ)."""
    items = activity.get("data") if isinstance(activity, dict) else None
    if not isinstance(items, list):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    totals: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "")
        if date and date < cutoff:
            continue
        model = str(item.get("model") or "—")
        usage = item.get("usage")
        if isinstance(usage, (int, float)):
            totals[model] = totals.get(model, 0) + float(usage)
    return [
        {"model": model, "usage": round(value, 2)}
        for model, value in sorted(totals.items(), key=lambda pair: -pair[1])[:8]
    ]


def openrouter_balance() -> dict:
    """OpenRouter: /credits (весь аккаунт) и /key (этот ключ).

    Разбивка по моделям и ключам доступна только management-ключом
    (`OPENROUTER_MANAGEMENT_KEY`); обычный ключ её не отдаёт.
    """
    checked_at = _iso()
    source = "OpenRouter API · /credits + /key"
    api_key = _env("OPENROUTER_API_KEY")
    if not api_key:
        return {
            "provider": "openrouter",
            "status": "not_configured",
            "source": source,
            "checked_at": checked_at,
            "captured_at": None,
            "data": None,
            "message": "Добавьте OPENROUTER_API_KEY (sk-or-…) в окружение Борта",
        }
    base = os.environ.get("BORT_OPENROUTER_API_URL", "https://openrouter.ai/api/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        credits = _get_json(f"{base}/credits", headers=headers)
        key_info = _get_json(f"{base}/key", headers=headers)
    except HTTPError as exc:
        message = f"OpenRouter вернул HTTP {exc.code}"
    except (URLError, TimeoutError):
        message = "OpenRouter недоступен или не ответил вовремя"
    except (ValueError, TypeError, KeyError):
        message = "OpenRouter вернул неожиданный формат ответа"
    else:
        account = credits.get("data") if isinstance(credits, dict) else None
        key_data = key_info.get("data") if isinstance(key_info, dict) else None
        total = account.get("total_credits") if isinstance(account, dict) else None
        used = account.get("total_usage") if isinstance(account, dict) else None
        if not isinstance(total, (int, float)) or not isinstance(used, (int, float)):
            message = "OpenRouter не вернул баланс аккаунта"
        else:
            remaining = round(float(total) - float(used), 2)
            windows = [
                {
                    "label": "Остаток на аккаунте",
                    "used_percent": _percent((float(used) / float(total) * 100) if total else 0),
                    "remaining_percent": _percent((remaining / float(total) * 100) if total else 0),
                    "window_minutes": None,
                    "resets_at": None,
                    "detail": f"израсходовано ${float(used):.2f} из пополнений ${float(total):.2f}",
                }
            ]
            balance = {
                "remaining": remaining,
                "usage": round(float(used), 2),
                "credits": round(float(total), 2),
                "remaining_percent": windows[0]["remaining_percent"],
                "used_percent": windows[0]["used_percent"],
            }
            key_usage = {}
            if isinstance(key_data, dict):
                key_usage = {
                    "usage": key_data.get("usage"),
                    "daily": key_data.get("usage_daily"),
                    "weekly": key_data.get("usage_weekly"),
                    "monthly": key_data.get("usage_monthly"),
                    "limit_remaining": key_data.get("limit_remaining"),
                    "limit": key_data.get("limit"),
                    "is_free_tier": key_data.get("is_free_tier"),
                }
            management = _env("OPENROUTER_MANAGEMENT_KEY") or (
                api_key if isinstance(key_data, dict) and key_data.get("is_management_key") else ""
            )
            models: list[dict] = []
            keys_top: list[dict] = []
            management_note = None
            if management:
                mgmt_headers = {"Authorization": f"Bearer {management}", "Accept": "application/json"}
                try:
                    activity = _get_json(f"{base}/activity", headers=mgmt_headers)
                    models = _openrouter_models(activity)
                except (HTTPError, URLError, TimeoutError, ValueError, TypeError):
                    management_note = "Management-ключ не дал разбивку по моделям"
                try:
                    keys_payload = _get_json(f"{base}/keys", headers=mgmt_headers)
                    items = keys_payload.get("data") if isinstance(keys_payload, dict) else None
                    if isinstance(items, list):
                        entries = [
                            {
                                "name": str(item.get("name") or item.get("label") or "—"),
                                "usage": round(float(item.get("usage") or 0), 4),
                            }
                            for item in items
                            if isinstance(item, dict)
                        ]
                        keys_top = sorted(entries, key=lambda entry: -entry["usage"])[:8]
                except (HTTPError, URLError, TimeoutError, ValueError, TypeError):
                    management_note = management_note or "Management-ключ не дал список ключей"
                if management_note is None and not models and not keys_top:
                    management_note = "Management-ключ подключён, но активности за 7 дней нет"
            else:
                management_note = "Расход по моделям и ключам — только management-ключом OpenRouter"
            captured_at = _iso()
            return {
                "provider": "openrouter",
                "status": "available",
                "source": source,
                "checked_at": captured_at,
                "captured_at": captured_at,
                "data": {
                    "windows": windows,
                    "balance": balance,
                    "key_usage": key_usage,
                    "models": models,
                    "api_keys": keys_top,
                    "management_note": management_note,
                },
                "message": None,
            }
    return {
        "provider": "openrouter",
        "status": "error",
        "source": source,
        "checked_at": checked_at,
        "captured_at": None,
        "data": None,
        "message": message,
    }
