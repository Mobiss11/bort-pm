"""AI MVP: изолированная БД, провайдеры без реальных сетевых вызовов."""

import json
import os

from bort import config
from bort.services import ai_providers


def test_ai_page_and_empty_overview(client):
    html = client.get("/ai")
    assert html.status_code == 200
    assert 'href="/ai"' in html.text
    assert "Codex" in html.text and "DeepSeek" in html.text
    assert "Qwen" in html.text and "Z.AI" in html.text and "OpenRouter" in html.text
    data = client.get("/api/v1/ai/overview").json()
    assert data["usage"]["today"]["requests"] == 0
    assert data["providers"]["codex"]["status"] == "not_checked"
    assert set(data["providers"]) == {"claude", "deepseek", "codex", "qwen", "zai", "openrouter"}


def test_record_and_list_usage(client):
    payload = {
        "provider": "deepseek", "agent": "OpenCode", "model": "deepseek-chat",
        "external_request_id": "req-1", "input_tokens": 100, "output_tokens": 22,
        "cache_read_tokens": 70, "cost_microusd": 4500,
    }
    response = client.post("/api/v1/ai/usage", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["input_tokens"] == 100
    assert client.post("/api/v1/ai/usage", json=payload).status_code == 409
    data = client.get("/api/v1/ai/overview").json()
    assert data["usage"]["today"]["requests"] == 1
    assert data["usage"]["today"]["cost_microusd"] == 4500
    assert len(client.get("/api/v1/ai/usage?provider=deepseek").json()["items"]) == 1
    assert "$0.0045" in client.get("/ai").text


def test_metadata_only_and_validation(client):
    base = {"provider": "deepseek", "model": "deepseek-chat"}
    for extra in ({"prompt": "secret"}, {"input_tokens": -2}, {"occurred_at": "not a date"}):
        assert client.post("/api/v1/ai/usage", json={**base, **extra}).status_code == 422
    assert client.post("/api/v1/ai/usage", json=base).status_code == 201
    assert "prompt" not in client.get("/api/v1/ai/usage").text


def test_codex_refresh_shows_source_and_account(client, monkeypatch):
    snapshot = {
        "provider": "codex", "status": "available", "source": "Codex CLI · account/rateLimits/read",
        "checked_at": "2026-09-27T10:00:00Z", "captured_at": "2026-09-27T10:00:00Z",
        "data": {"account_email": "user@example.com", "plan_type": "pro", "buckets": [
            {"id": "codex", "name": "codex", "primary": {"remaining_percent": 75,
              "window_minutes": 300, "resets_at": "2026-09-27T15:00:00Z"}, "secondary": None}
        ]}, "message": "Сверьте эту учётную запись с авторизацией GPT в OpenCode",
    }
    monkeypatch.setattr(ai_providers, "codex_limits", lambda: snapshot)
    response = client.post("/api/v1/ai/providers/codex/refresh")
    assert response.status_code == 200
    assert response.json()["data"]["buckets"][0]["primary"]["remaining_percent"] == 75
    html = client.get("/ai").text
    assert "75% осталось" in html and "user@example.com" in html
    assert "Сверьте" in html


def test_codex_unavailable_without_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("BORT_CODEX_LIMITS_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(ai_providers.shutil, "which", lambda *_: None)
    result = ai_providers.codex_limits()
    assert result["status"] == "unavailable"
    assert result["data"] is None


def test_failed_refresh_keeps_previous_codex_data(client, tmp_path, monkeypatch):
    """Сбойный снимок (нет CLI/вход) не стирает прежние цифры — как на дашборде."""
    path = tmp_path / "codex-limits.json"
    monkeypatch.setenv("BORT_CODEX_LIMITS_FILE", str(path))
    import datetime as dt

    path.write_text(json.dumps({
        "version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "available",
        "message": None,
        "data": {"account_email": "user@example.com", "plan_type": "prolite", "buckets": [
            {"id": "codex", "name": "codex", "primary": {"remaining_percent": 26, "window_minutes": 10080,
                                                         "resets_at": "2026-10-03T17:14:27Z"}, "secondary": None}]},
    }))
    assert client.post("/api/v1/ai/providers/codex/refresh").json()["status"] == "available"

    path.write_text(json.dumps({
        "version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "unavailable",
        "message": "Нет доступных лимитов Codex CLI",
        "data": None,
    }))
    data = client.post("/api/v1/ai/providers/codex/refresh").json()
    assert data["status"] == "unavailable"
    assert data["data"]["buckets"][0]["primary"]["remaining_percent"] == 26  # цифры не пропали
    html = client.get("/ai").text
    assert "26% осталось" in html and "Нет доступных лимитов Codex CLI" in html


def test_codex_limits_from_macbook_snapshot(client, tmp_path, monkeypatch):
    path = tmp_path / "codex-limits.json"
    monkeypatch.setenv("BORT_CODEX_LIMITS_FILE", str(path))
    # Без снимка и без CLI — недоступно (на mini CLI нет).
    monkeypatch.setattr(ai_providers.shutil, "which", lambda *_: None)
    assert ai_providers.codex_limits()["status"] == "unavailable"

    import datetime as dt

    path.write_text(json.dumps({
        "version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "available",
        "message": "Сверьте эту учётную запись с авторизацией GPT в OpenCode",
        "data": {
            "account_email": "user@example.com",
            "plan_type": "prolite",
            "buckets": [{"id": "codex", "name": "codex", "primary": {
                "remaining_percent": 26, "window_minutes": 10080, "resets_at": "2026-10-03T17:14:27Z",
            }, "secondary": None}],
        },
    }))
    data = client.post("/api/v1/ai/providers/codex/refresh").json()
    assert data["status"] == "available"
    assert data["data"]["plan_type"] == "prolite"
    assert "с MacBook" in data["source"]
    assert (tmp_path / "codex-refresh.request").is_file()  # POST просит MacBook перечитать CLI
    html = client.get("/ai").text
    assert "26% осталось" in html and "user@example.com" in html

    # Устаревший снимок помечается предупреждением
    path.write_text(json.dumps({
        "version": 1,
        "captured_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
        "status": "available",
        "message": None,
        "data": {"account_email": "user@example.com", "plan_type": "prolite", "buckets": []},
    }))
    data = client.post("/api/v1/ai/providers/codex/refresh").json()
    assert data["status"] == "available" and "старше" in data["message"]


def test_codex_rpc_parsing(monkeypatch):
    class Input:
        def __init__(self):
            self.messages = []

        def write(self, message):
            self.messages.append(json.loads(message))

        def flush(self):
            pass

    class Output:
        def readline(self):
            return '{"id": 1, "result": {"rateLimits": {"primary": {"usedPercent": 25}}}}\n'

    class Process:
        stdin = Input()
        stdout = Output()

    monkeypatch.setattr(ai_providers.select, "select", lambda *_: ([Process.stdout], [], []))
    result = ai_providers._codex_rpc(Process(), "account/rateLimits/read", 1)
    assert result["rateLimits"]["primary"]["usedPercent"] == 25
    assert Process.stdin.messages[-1]["method"] == "account/rateLimits/read"
    assert ai_providers._codex_window({"usedPercent": 25, "windowDurationMins": 300})["remaining_percent"] == 75


def test_deepseek_balance_refresh(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")

    def fake_urlopen(request, timeout):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps({"is_available": True, "balance_infos": [
                    {"currency": "USD", "total_balance": "5.00", "granted_balance": "1.00",
                     "topped_up_balance": "4.00"}
                ]}).encode()
        return Response()

    monkeypatch.setattr(ai_providers, "urlopen", fake_urlopen)
    data = client.post("/api/v1/ai/providers/deepseek/refresh").json()
    assert data["data"]["balances"][0]["total_balance"] == "5.00"
    assert "5.00" in client.get("/ai").text


def test_env_file_loads_without_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "from-env")
    path = tmp_path / "env"
    path.write_text("# секрет\nZAI_API_KEY=from-file\nDEEPSEEK_API_KEY=file-deepseek\nBAD LINE\n")
    assert config.load_env_file(path) == 1
    assert os.environ["ZAI_API_KEY"] == "from-env"          # окружение приоритетнее
    assert os.environ["DEEPSEEK_API_KEY"] == "file-deepseek"
    assert config.load_env_file(tmp_path / "missing") == 0


def test_qwen_plan_reads_macbook_snapshot(client, tmp_path, monkeypatch):
    path = tmp_path / "qwencloud-usage.json"
    monkeypatch.setenv("BORT_QWENCLOUD_FILE", str(path))

    # Снимка нет — карточка честно просит его сделать.
    data = client.post("/api/v1/ai/providers/qwen/refresh").json()
    assert data["status"] == "not_configured"

    import datetime as dt

    path.write_text(json.dumps({
        "version": 1,
        "status": "ok",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "plan": {"spec": "pro", "remaining_days": 30},
        "credits": {"total": 180000, "remaining": 165780, "used_percent": 7.9,
                    "reset_at": "2026-10-30T16:00:00Z"},
    }))
    data = client.post("/api/v1/ai/providers/qwen/refresh").json()
    assert data["status"] == "available"
    assert data["data"]["plan"] == "pro"
    window = data["data"]["windows"][0]
    assert window["remaining_percent"] == 92.1
    assert "165 780 из 180 000" in window["detail"]
    html = client.get("/ai").text
    assert "Qwen Cloud · Token Plan" in html and "165 780 из 180 000" in html
    assert "home.qwencloud.com/analytics/token-plan/individual" in html

    # Просроченный вход
    path.write_text(json.dumps({"version": 1, "status": "login_required", "captured_at": "2026-09-30T10:00:00Z"}))
    data = client.post("/api/v1/ai/providers/qwen/refresh").json()
    assert data["status"] == "auth_required"
    assert "залогинен" in data["message"]

    # Устаревший снимок остаётся данными, но с предупреждением
    path.write_text(json.dumps({
        "version": 1,
        "status": "ok",
        "captured_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).isoformat().replace("+00:00", "Z"),
        "plan": {"spec": "pro", "remaining_days": 30},
        "credits": {"total": 180000, "remaining": 100000, "used_percent": 44.4},
    }))
    data = client.post("/api/v1/ai/providers/qwen/refresh").json()
    assert data["status"] == "available"
    assert "старше" in data["message"]


def test_zai_limits_parses_and_renders(client, monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "test-secret")
    payload = {
        "success": True,
        "code": 200,
        "data": {
            "planName": "GLM Coding Pro",
            "limits": [
                {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 25, "nextResetTime": 1796000000000},
                {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 60},
                # проценты врут, но usage/remaining честнее — пересчитываем (использовано 80%)
                {"type": "TOKENS_LIMIT", "unit": 1, "number": 1, "percentage": 99,
                 "usage": 100, "remaining": 20, "currentValue": 80},
                {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 10},
            ],
        },
    }
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps(payload).encode()

        return Response()

    monkeypatch.setattr(ai_providers, "urlopen", fake_urlopen)
    data = client.post("/api/v1/ai/providers/zai/refresh").json()
    assert data["status"] == "available"
    assert seen["url"].endswith("/api/monitor/usage/quota/limit")
    assert seen["auth"] == "Bearer test-secret"
    windows = {window["label"]: window for window in data["data"]["windows"]}
    assert windows["5 часов"]["used_percent"] == 25
    assert windows["7 дней"]["used_percent"] == 60
    assert windows["1 дн."]["used_percent"] == 80
    assert windows["MCP"]["used_percent"] == 10
    html = client.get("/ai").text
    assert "Z.AI" in html and "25% использовано" in html and "GLM Coding Pro" in html


def test_zai_credit_limits_are_normalized(client, monkeypatch):
    """CREDIT_LIMIT отдаёт кредиты (usage/remaining), а не проценты — считаем сами."""
    monkeypatch.setenv("ZAI_API_KEY", "test-secret")
    payload = {
        "code": 200,
        "msg": "Operation successful",
        "success": True,
        "data": {
            "level": "pro",
            "limits": [
                {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 12000,
                 "currentValue": 2502, "remaining": 9497, "percentage": 20, "nextResetTime": 1790788503760},
                {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 60000,
                 "currentValue": 31746, "remaining": 28253, "percentage": 52, "nextResetTime": 1791191091983},
            ],
        },
    }
    monkeypatch.setattr(ai_providers, "urlopen", _router_urlopen(
        {"https://api.z.ai/api/monitor/usage/quota/limit": payload}
    ))
    data = client.post("/api/v1/ai/providers/zai/refresh").json()
    assert data["status"] == "available"
    assert data["data"]["plan"] == "pro"
    windows = {window["label"]: window for window in data["data"]["windows"]}
    assert windows["5 часов"]["used_percent"] == 20.9
    assert windows["5 часов"]["credits_used"] == 2503 and windows["5 часов"]["credits_total"] == 12000
    assert windows["7 дней"]["used_percent"] == 52.9
    html = client.get("/ai").text
    assert "20.9% использовано" in html and "Кредиты: 2 503 / 12 000" in html
    assert "Кредиты: 31 747 / 60 000" in html


def test_zai_not_configured_and_bad_payload(monkeypatch):
    result = ai_providers.zai_limits()
    assert result["status"] == "not_configured"

    monkeypatch.setenv("ZAI_API_KEY", "test-secret")

    def fake_urlopen(request, timeout):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps({"success": True, "code": 200, "data": {"limits": "нет"}}).encode()

        return Response()

    monkeypatch.setattr(ai_providers, "urlopen", fake_urlopen)
    result = ai_providers.zai_limits()
    assert result["status"] == "error"
    assert "формат" in result["message"]


def _router_urlopen(routes: dict):
    def fake_urlopen(request, timeout):
        url = request.full_url
        if url not in routes:
            raise AssertionError(f"неожиданный URL: {url}")
        payload = routes[url]

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps(payload).encode()

        return Response()

    return fake_urlopen


def test_openrouter_balance_and_key_usage(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    routes = {
        "https://openrouter.ai/api/v1/credits": {"data": {"total_credits": 583.5, "total_usage": 574.605955684}},
        "https://openrouter.ai/api/v1/key": {"data": {"usage": 0, "usage_daily": 0, "usage_weekly": 0, "usage_monthly": 0,
                                                      "limit": None, "limit_remaining": None, "is_free_tier": False}},
    }
    monkeypatch.setattr(ai_providers, "urlopen", _router_urlopen(routes))
    data = client.post("/api/v1/ai/providers/openrouter/refresh").json()
    assert data["status"] == "available"
    balance = data["data"]["balance"]
    assert balance["remaining"] == 8.89 and balance["usage"] == 574.61 and balance["credits"] == 583.5
    assert "management" in data["data"]["management_note"]
    html = client.get("/ai").text
    assert "OpenRouter" in html and "Остаток на аккаунте" in html and "$8.89" in html
    assert "израсходовано $574.61 из пополнений $583.50" in html
    assert "$8.89 из $583.50" not in html


def test_openrouter_not_configured(monkeypatch):
    assert ai_providers.openrouter_balance()["status"] == "not_configured"


def test_openrouter_management_activity_models(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENROUTER_MANAGEMENT_KEY", "sk-or-mgmt")
    routes = {
        "https://openrouter.ai/api/v1/credits": {"data": {"total_credits": 100.0, "total_usage": 40.0}},
        "https://openrouter.ai/api/v1/key": {"data": {"usage": 5.0, "usage_daily": 1.0, "usage_weekly": 5.0,
                                                      "usage_monthly": 5.0, "limit": 20.0, "limit_remaining": 15.0}},
        "https://openrouter.ai/api/v1/activity": {"data": [
            {"date": "2999-01-01", "model": "openai/gpt-6.1-sol", "usage": 2.5},
            {"date": "2999-01-01", "model": "qwen/qwen3.8-max", "usage": 1.0},
            {"date": "2000-01-01", "model": "старое/не-учитывать", "usage": 99.0},
        ]},
        "https://openrouter.ai/api/v1/keys": {"data": [
            {"name": "hermes", "usage": 38.5},
            {"name": "agents", "usage": 6.99},
            {"name": "bort", "usage": 0},
        ]},
    }
    monkeypatch.setattr(ai_providers, "urlopen", _router_urlopen(routes))
    data = client.post("/api/v1/ai/providers/openrouter/refresh").json()
    models = {row["model"]: row["usage"] for row in data["data"]["models"]}
    assert models == {"openai/gpt-6.1-sol": 2.5, "qwen/qwen3.8-max": 1.0}
    keys = [row["name"] for row in data["data"]["api_keys"]]
    assert keys == ["hermes", "agents", "bort"]
    html = client.get("/ai").text
    assert "openai/gpt-6.1-sol" in html and "$2.5000" in html
    assert "Расход по моделям за 7 дней" in html and "hermes" in html and "$38.5000" in html


def test_iso_local_shows_bort_timezone():
    from bort.web.filters import iso_local

    assert iso_local("2026-10-01T11:22:14Z") == "01.10.2026 14:22"  # Europe/Moscow
    assert iso_local(None) == "—" and iso_local("мусор") == "—"


_QWEN_OK_SNAPSHOT = {
    "version": 1,
    "status": "ok",
    "captured_at": "2026-10-01T11:00:00Z",
    "plan": {"spec": "pro", "remaining_days": 30},
    "credits": {"total": 180000, "remaining": 165780, "used_percent": 7.9,
                "reset_at": "2026-10-30T16:00:00Z"},
}


def test_ai_page_orders_subscriptions_before_balances(client):
    """Подписки (codex/qwen/zai) идут раньше балансов (deepseek/openrouter)."""
    html = client.get("/ai").text
    order = ['id="codex-card"', 'id="qwen-card"', 'id="zai-card"',
             'id="deepseek-card"', 'id="openrouter-card"']
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert "Обновить всё" in html
    assert 'hx-post="/ui/ai/refresh-all"' in html
    assert 'hx-target="#provider-grid"' in html


def test_refresh_all_route_renders_every_card_and_isolates_failure(client, monkeypatch):
    def boom():
        raise RuntimeError("провайдер упал")

    monkeypatch.setattr(ai_providers, "deepseek_balance", boom)
    response = client.post("/ui/ai/refresh-all")
    assert response.status_code == 200, response.text
    for card in ("codex-card", "qwen-card", "zai-card", "deepseek-card", "openrouter-card"):
        assert f'id="{card}"' in response.text
    # Сбой глубокого провайдера сохранён со статусом ошибки, остальные обновились.
    providers = client.get("/api/v1/ai/overview").json()["providers"]
    assert providers["deepseek"]["status"] == "error"
    assert "Обновление не удалось" in providers["deepseek"]["message"]
    assert providers["zai"]["status"] == "not_configured"
    assert providers["openrouter"]["status"] == "not_configured"


def test_refresh_all_places_macbook_requests_for_codex_and_qwen(client, tmp_path, monkeypatch):
    codex_file = tmp_path / "codex-limits.json"
    qwen_file = tmp_path / "qwencloud-usage.json"
    monkeypatch.setenv("BORT_CODEX_LIMITS_FILE", str(codex_file))
    monkeypatch.setenv("BORT_QWENCLOUD_FILE", str(qwen_file))
    codex_file.write_text(json.dumps({
        "version": 1, "captured_at": "2026-10-01T11:00:00Z", "status": "available", "message": None,
        "data": {"account_email": "u@e", "plan_type": "pro", "buckets": []},
    }))
    qwen_file.write_text(json.dumps(_QWEN_OK_SNAPSHOT))
    assert client.post("/ui/ai/refresh-all").status_code == 200
    assert (tmp_path / "codex-refresh.request").is_file()
    assert (tmp_path / "qwen-refresh.request").is_file()


def test_qwen_refresh_request_only_on_post(client, tmp_path, monkeypatch):
    path = tmp_path / "qwencloud-usage.json"
    monkeypatch.setenv("BORT_QWENCLOUD_FILE", str(path))
    path.write_text(json.dumps(_QWEN_OK_SNAPSHOT))
    request_file = tmp_path / "qwen-refresh.request"
    # GET (автополлинг карточки) перечитывает снимок, но заявку не ставит.
    assert client.get("/ui/ai/providers/qwen/refresh").status_code == 200
    assert not request_file.is_file()
    # POST (кнопка «Обновить») ставит заявку MacBook-агенту.
    response = client.post("/ui/ai/providers/qwen/refresh")
    assert response.status_code == 200
    assert "Обновить" in response.text
    assert request_file.is_file()


def test_request_qwen_refresh_writes_iso(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "qwen-refresh.request"
    monkeypatch.setenv("BORT_QWEN_REQUEST_FILE", str(target))
    assert ai_providers.request_qwen_refresh() is True
    assert target.read_text(encoding="utf-8").endswith("Z")


def test_claude_card_not_configured_without_key(client, monkeypatch):
    monkeypatch.delenv("KRAUBE_SERVE_KEY", raising=False)
    response = client.post("/ui/ai/providers/claude/refresh")
    assert response.status_code == 200
    assert "claude-card" in response.text
    assert "KRAUBE_SERVE_KEY" in response.text
    assert "Не настроено" in response.text


def test_claude_limits_refresh_renders_windows(client, monkeypatch):
    monkeypatch.setenv("KRAUBE_SERVE_KEY", "test-kraube-key")

    def fake_urlopen(request, timeout):
        assert "usage" in str(request.full_url)
        assert request.headers.get("Authorization") == "Bearer test-kraube-key"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps({
                    "status": "allowed",
                    "claim": "five_hour",
                    "five_hour": {"used_percent": 12.5, "resets_at": "2026-10-04T14:50:00+03:00",
                                  "resets_in": "4h56m21s"},
                    "seven_day": {"used_percent": 3, "resets_at": "2026-10-09T10:00:00+03:00",
                                  "resets_in": "120h6m21s"},
                    "updated_at": "2026-10-04T09:53:26+03:00",
                    "age": "11s",
                }).encode()

        return Response()

    monkeypatch.setattr(ai_providers, "urlopen", fake_urlopen)
    response = client.post("/ui/ai/providers/claude/refresh")
    assert response.status_code == 200
    assert "claude-card" in response.text
    assert "5 часов" in response.text
    assert "7 дней" in response.text
    assert "12.5% использовано" in response.text
    assert "3% использовано" in response.text
    assert "Сброс через 4h56m21s" in response.text
    assert "Возраст кэша демона: 11s" in response.text

    page = client.get("/ai")
    assert "claude-card" in page.text
    api = client.post("/api/v1/ai/providers/claude/refresh").json()
    assert api["status"] == "available"
    assert api["data"]["unified_status"] == "allowed"
    assert api["data"]["windows"][0]["window_minutes"] == 300


def test_claude_limits_warning_status_and_capitals(client, monkeypatch):
    monkeypatch.setenv("KRAUBE_SERVE_KEY", "k")

    def fake_urlopen(request, timeout):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps({
                    "status": "allowed_warning",
                    "five_hour": {"used_percent": 150, "resets_at": "x"},
                    "seven_day": {"used_percent": 9},
                }).encode()

        return Response()

    monkeypatch.setattr(ai_providers, "urlopen", fake_urlopen)
    data = client.post("/api/v1/ai/providers/claude/refresh").json()
    assert data["status"] == "available"
    assert data["message"] == "Лимит подписки приближается — экономьте окно"
    five_hour = data["data"]["windows"][0]
    assert five_hour["used_percent"] == 100  # 150 обрезан до 100
    assert five_hour["resets_at"] is None  # невалидное время отброшено


def test_claude_limits_404_before_first_request(client, monkeypatch):
    monkeypatch.setenv("KRAUBE_SERVE_KEY", "k")

    def fake_urlopen(request, timeout):
        raise ai_providers.HTTPError(request.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(ai_providers, "urlopen", fake_urlopen)
    data = client.post("/api/v1/ai/providers/claude/refresh").json()
    assert data["status"] == "error"
    assert "после первого запроса" in data["message"]


def test_claude_limits_daemon_down(client, monkeypatch):
    monkeypatch.setenv("KRAUBE_SERVE_KEY", "k")

    def fake_urlopen(request, timeout):
        raise ai_providers.URLError("connection refused")

    monkeypatch.setattr(ai_providers, "urlopen", fake_urlopen)
    data = client.post("/api/v1/ai/providers/claude/refresh").json()
    assert data["status"] == "error"
    assert "недоступен" in data["message"]
