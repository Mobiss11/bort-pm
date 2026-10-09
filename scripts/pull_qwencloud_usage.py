"""MacBook -> mini: кредиты Qwen Cloud Token Plan (снимок из консоли).

Открывает консоль Qwen Cloud в headless Chrome с копией залогиненного профиля
пользователя, читает ответы внутренних API консоли (usage/subscription/quota-config)
и отправляет компактный снимок на mini. Cookies и токены не сохраняются и никуда
не пишутся: используется та же сессия, что в браузере.

Запуск (MacBook, есть Python с playwright и Chrome):
    python3 scripts/pull_qwencloud_usage.py

Переменные: BORT_BEADS_SYNC_HOST / BORT_BEADS_SYNC_HOST_KEY_ALIAS — адрес mini
(те же, что у агента Beads); BORT_QWENCLOUD_SYNC_HOST / ..._KEY_ALIAS — переопределение;
BORT_CHROME_PROFILE — каталог профиля Chrome (Default, Profile 1, ...).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CONSOLE_URL = "https://home.qwencloud.com/analytics/token-plan/individual"
CHROME_ROOT = Path.home() / "Library/Application Support/Google/Chrome"


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_host(value: str) -> str:
    value = (value or "").strip()
    if not value or value.startswith("-") or any(ch.isspace() for ch in value):
        return ""
    return value


def _pick_profile() -> Path | None:
    explicit = os.environ.get("BORT_CHROME_PROFILE", "").strip()
    if explicit:
        path = CHROME_ROOT / explicit
        return path if (path / "Cookies").is_file() else None
    for name in ("Default", "Profile 1", "Profile 2", "Profile 3"):
        if (CHROME_ROOT / name / "Cookies").is_file():
            return CHROME_ROOT / name
    return None


def _unwrap(payload: dict) -> dict:
    """Достать полезные данные из конверта console-gateway."""
    data = (payload or {}).get("data") or {}
    inner = (data.get("DataV2") or {}).get("data") or {}
    if data.get("errorCode"):
        return {"_error": data["errorCode"]}
    return inner.get("data") if isinstance(inner.get("data"), dict) else {}


def collect() -> dict:
    profile = _pick_profile()
    if profile is None:
        return {"status": "error", "message": "профиль Chrome не найден (нет Cookies)"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "error", "message": "playwright не установлен для этого Python"}

    work = Path(tempfile.mkdtemp(prefix="qwcloud-"))
    temp_profile = work / "prof"
    (temp_profile / "Default").mkdir(parents=True)
    shutil.copy2(CHROME_ROOT / "Local State", temp_profile / "Local State")
    shutil.copy2(profile / "Cookies", temp_profile / "Default" / "Cookies")

    captured: dict[str, dict] = {}
    login_seen = False
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(temp_profile), headless=True, executable_path=CHROME,
                ignore_default_args=["--use-mock-keychain"],
                args=["--no-first-run", "--no-default-browser-check", "--no-sandbox"],
            )
            page = ctx.new_page()

            def on_response(resp):
                nonlocal login_seen
                url = unquote(resp.url)
                if "account/info" in url:
                    try:
                        body = resp.json()
                    except Exception:
                        return
                    if (body.get("data") or {}).get("code") == "ConsoleNeedLogin" or body.get("code") == "ConsoleNeedLogin":
                        login_seen = True
                for key in ("v2/usage", "v2/subscription", "v2/quota-config"):
                    if key in url and key not in captured:
                        try:
                            captured[key] = resp.json()
                        except Exception:
                            pass

            page.on("response", on_response)
            for attempt in range(3):
                try:
                    page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
                deadline = datetime.now()
                while (datetime.now() - deadline).total_seconds() < 25 and "v2/usage" not in captured:
                    page.wait_for_timeout(500)
                if "v2/usage" in captured or login_seen:
                    break
                page.wait_for_timeout(1000)
            ctx.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if login_seen and "v2/usage" not in captured:
        return {"status": "login_required", "message": "Chrome не залогинен в консоли Qwen Cloud"}
    if "v2/usage" not in captured:
        return {"status": "error", "message": "консоль не отдала окна квот (не залогинена или изменилась)"}

    usage = _unwrap(captured["v2/usage"])
    quota = _unwrap(captured.get("v2/quota-config", {}))
    subscription = _unwrap(captured.get("v2/subscription", {}))

    spec = str(subscription.get("specCode") or "").strip().lower()
    total = None
    if isinstance(quota, dict) and spec in quota and isinstance(quota[spec], dict):
        total = quota[spec].get("monthly")
    used = usage.get("per1MonthPercentage")
    reset = usage.get("per1MonthResetTime")
    result = {
        "version": 1,
        "status": "ok",
        "captured_at": _iso(),
        "plan": {
            "spec": spec or None,
            "instance": subscription.get("instanceCode"),
            "remaining_days": subscription.get("remainingDays"),
        },
        "credits": {
            "total": int(total) if isinstance(total, (int, float)) else None,
            "used_percent": round(float(used) * 100, 1) if isinstance(used, (int, float)) else None,
            "reset_at": (
                _iso(datetime.fromtimestamp(reset / 1000, tz=timezone.utc))
                if isinstance(reset, (int, float)) and reset > 0 else None
            ),
        },
    }
    credits = result["credits"]
    if credits["total"] and credits["used_percent"] is not None:
        remaining = round(credits["total"] * (100 - credits["used_percent"]) / 100)
        credits["remaining"] = remaining
        credits["used"] = credits["total"] - remaining
    return result


def push(document: dict) -> int:
    host = _sanitize_host(os.environ.get("BORT_QWENCLOUD_SYNC_HOST") or os.environ.get("BORT_BEADS_SYNC_HOST", ""))
    key_alias = _sanitize_host(
        os.environ.get("BORT_QWENCLOUD_SYNC_HOST_KEY_ALIAS") or os.environ.get("BORT_BEADS_SYNC_HOST_KEY_ALIAS", "")
    )
    if not host:
        print("BORT_BEADS_SYNC_HOST не настроен", file=sys.stderr)
        return 1
    payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
    remote = (
        "import os,pathlib,sys,tempfile; "
        "root=pathlib.Path.home()/\"bort/data\"; root.mkdir(parents=True,exist_ok=True); "
        "fd,name=tempfile.mkstemp(prefix=\".qwencloud-\",dir=root); "
        "f=os.fdopen(fd,\"wb\"); f.write(sys.stdin.buffer.read()); f.close(); "
        "os.chmod(name,0o600); os.replace(name,root/\"qwencloud-usage.json\")"
    )
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if key_alias:
        command.extend(["-o", f"HostKeyAlias={key_alias}"])
    try:
        subprocess.run([*command, host, "python3 -c " + __import__("shlex").quote(remote)],
                       input=payload, timeout=25, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Не удалось отправить снимок ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"Снимок Qwen Cloud отправлен: {document.get('status')}")
    return 0


def main() -> int:
    document = collect()
    print(json.dumps({k: v for k, v in document.items() if k != "message"}, ensure_ascii=False))
    if document.get("message"):
        print(document["message"], file=sys.stderr)
    return push(document)


if __name__ == "__main__":
    raise SystemExit(main())
