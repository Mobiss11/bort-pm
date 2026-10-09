"""Read-only Beads: discovery, CLI isolation, filters, errors and page states."""

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bort import errors
from bort.services import beads
from bort.services import beads_links, projects as projects_svc


@pytest.fixture()
def projects(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    for name in ("Напор", "second"):
        marker = root / name / ".beads"
        marker.mkdir(parents=True)
        (marker / "config.yaml").write_text("issue-prefix: t\n")
    (root / "unrelated").mkdir()
    nested = root / "unrelated" / "nested" / ".beads"
    nested.mkdir(parents=True)
    (nested / "config.yaml").write_text("issue-prefix: t\n")
    (root / "linked").symlink_to(root / "Напор", target_is_directory=True)
    monkeypatch.setenv("BORT_BEADS_ROOTS", str(root))
    monkeypatch.setenv("BORT_BEADS_BIN", "/fake/bd")
    beads._cache.clear()
    yield root
    beads._cache.clear()


@pytest.fixture()
def fake_bd(projects, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if "ready" in args:
            issues = [{"id": "t-1"}]
        else:
            issues = [
                {"id": "t-1", "title": "<script>Проверить</script>", "status": "open", "priority": 0, "issue_type": "bug", "description": "private"},
                {"id": "t-2", "title": "Готовить релиз", "status": "in_progress", "priority": 2, "issue_type": "task"},
                {"id": "t-3", "title": "Сделано", "status": "closed", "priority": 4, "issue_type": "task"},
            ]
        return subprocess.CompletedProcess(args, 0, json.dumps(issues), "")
    monkeypatch.setattr(beads.subprocess, "run", run)
    return calls


def test_discovery_nonrecursive_and_cache(projects, fake_bd):
    assert set(beads.discover_projects()) == {"Напор", "second", "nested"}
    data = beads.snapshots()
    assert len(data) == 3
    assert len(fake_bd) == 6
    assert beads.snapshots() == data
    assert len(fake_bd) == 6
    beads.snapshots(refresh=True)
    assert len(fake_bd) == 12
    for args in fake_bd:
        assert args[:4] == ["/fake/bd", "-C", args[2], "--readonly"]
        assert args[-3:] == ["--json", "--limit", "0"]


def test_page_loading_filters_and_escaping(client, projects, fake_bd):
    loading = client.get("/tasks")
    assert loading.status_code == 200
    assert 'hx-get="/ui/beads"' in loading.text
    assert "Загружаем проекты" in loading.text
    assert ">Задачи</a>" in loading.text
    assert not fake_bd
    overview = client.get("/ui/beads")
    assert overview.status_code == 200
    assert "Напор" in overview.text and "second" in overview.text
    page = client.get("/tasks", params={"project": "Напор"})
    assert page.status_code == 200
    assert "Можно брать" in page.text
    assert "private" not in page.text
    assert "<script>Проверить</script>" not in page.text
    assert "&lt;script&gt;Проверить&lt;/script&gt;" in page.text
    assert "Сделано" not in page.text
    assert 'class="beads-kanban"' in page.text
    assert "Заблокированы" in page.text
    filtered = client.get("/tasks", params={"project": "Напор", "status": "closed", "priority": "4", "type": "task", "q": "Сделано", "view": "list"})
    assert filtered.status_code == 200
    assert "Сделано" in filtered.text
    assert "Проверить" not in filtered.text
    assert 'class="beads-issues"' in filtered.text
    assert client.get("/tasks", params={"project": "../bad"}).status_code == 404
    assert client.get("/tasks", params={"project": "Напор", "priority": "-1"}).status_code == 422
    assert client.get("/tasks", params={"project": "Напор", "view": "bad"}).status_code == 422
    assert client.get("/beads?project=second", follow_redirects=False).headers["location"] == "/tasks?project=second"


def test_empty_and_failure(client, projects, monkeypatch):
    monkeypatch.setenv("BORT_BEADS_ROOTS", str(projects / "empty"))
    page = client.get("/tasks?full=1")
    assert page.status_code == 200
    assert "Доступных проектов с задачами пока нет" in page.text
    monkeypatch.setenv("BORT_BEADS_ROOTS", str(projects))
    def fail(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 12)
    monkeypatch.setattr(beads.subprocess, "run", fail)
    page = client.get("/tasks?full=1")
    assert page.status_code == 200
    assert "Доступных проектов с задачами пока нет" in page.text
    assert "second" not in page.text
    assert client.get("/tasks?project=second").status_code == 404


def test_failed_refresh_excludes_previous_data(client, projects, fake_bd, monkeypatch):
    assert beads.snapshots()[0]["issues"]
    def fail(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "private error/path")
    monkeypatch.setattr(beads.subprocess, "run", fail)
    page = client.get("/ui/beads?refresh=1")
    assert "private error/path" not in page.text
    assert "Готовить релиз" not in page.text
    assert "second" not in page.text
    selected = client.get("/ui/beads?project=second&refresh=1")
    assert selected.status_code == 200
    assert "Выберите проект" in selected.text or "Доступных проектов" in selected.text


def test_empty_project_and_missing_cli(client, projects, monkeypatch):
    def empty(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, "[]", "")
    monkeypatch.setattr(beads.subprocess, "run", empty)
    page = client.get("/tasks?project=second")
    assert page.status_code == 200
    assert "В этом проекте пока нет задач" in page.text
    monkeypatch.setenv("BORT_BEADS_BIN", "")
    monkeypatch.setattr(beads.shutil, "which", lambda _: None)
    page = client.get("/tasks?full=1&refresh=1")
    assert "Доступных проектов с задачами пока нет" in page.text


def test_bad_json_is_error(client, projects, monkeypatch):
    monkeypatch.setattr(beads.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "not-json", ""))
    page = client.get("/tasks?full=1")
    assert page.status_code == 200
    assert "Доступных проектов с задачами пока нет" in page.text


def test_link_from_summary_replace_unlink_and_reject_unknown(client, projects, fake_bd, conn):
    # Отдельная БД сервиса проверяется прямым вызовом; UI-тест создаёт запись в client DB.
    project = projects_svc.create_project(conn, {"name": "Рабочий проект"})
    beads_links.set_link(conn, project["id"], "second", {"second"})
    assert beads_links.all_links(conn)[0][project["id"]] == "second"
    beads_links.remove_link(conn, project["id"])
    assert beads_links.all_links(conn)[0] == {}
    assert client.post("/api/v1/projects", json={"name": "Напор в сводке"}).status_code == 201
    pid = client.get("/api/v1/projects").json()["items"][0]["id"]
    summary = client.get("/")
    assert f"/tasks?link_project_id={pid}" in summary.text
    wizard = client.get(f"/tasks?link_project_id={pid}")
    assert "Связать «Напор в сводке»" in wizard.text
    assert "second" in wizard.text
    assert client.post(f"/tasks/links/{pid}", data={"beads_project": "../bad"}).status_code == 422
    response = client.post(f"/tasks/links/{pid}", data={"beads_project": "second"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/tasks?project=second"
    assert "Открыть задачи" in client.get("/").text
    assert "Напор в сводке" in client.get("/tasks?project=second").text
    assert client.post(f"/tasks/links/{pid}/remove", follow_redirects=False).status_code == 303
    assert "Привязать к задачам" in client.get("/").text


def test_link_uniqueness_and_readonly_visibility(client, projects, fake_bd, conn, monkeypatch):
    one = projects_svc.create_project(conn, {"name": "Первый"})
    two = projects_svc.create_project(conn, {"name": "Второй"})
    beads_links.set_link(conn, one["id"], "Напор", {"Напор", "second"})
    with pytest.raises(errors.Conflict):
        beads_links.set_link(conn, two["id"], "Напор", {"Напор", "second"})
    assert beads_links.all_links(conn)[0] == {one["id"]: "Напор"}
    beads_links.set_link(conn, one["id"], "second", {"Напор", "second"})
    assert beads_links.all_links(conn)[0] == {one["id"]: "second"}

    # Путь через веб: недоступный проект не предлагается для новой связи.
    pid = client.post("/api/v1/projects", json={"name": "Второй в UI"}).json()["id"]
    assert client.get(f"/tasks?link_project_id={pid}").status_code == 200
    def fail_second(args, **kwargs):
        if "second" in args[2]:
            return subprocess.CompletedProcess(args, 1, "", "Error: no beads database found")
        return subprocess.CompletedProcess(args, 0, json.dumps([{"id": "t-1", "title": "x", "status": "open"}]), "")
    monkeypatch.setattr(beads.subprocess, "run", fail_second)
    page = client.get(f"/tasks?link_project_id={pid}&refresh=1")
    assert '<option value="second"' not in page.text
    assert client.post(f"/tasks/links/{pid}", data={"beads_project": "second"}).status_code == 422


def test_snapshot_mode_reads_without_cli_and_marks_stale(client, tmp_path, monkeypatch):
    path = tmp_path / "beads-snapshot.json"
    monkeypatch.setenv("BORT_BEADS_SNAPSHOT", str(path))
    monkeypatch.setattr(beads.subprocess, "run", lambda *a, **k: pytest.fail("bd must not run on mini"))
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
    path.write_text(json.dumps({"version": 1, "generated_at": stamp, "projects": [{
        "name": "Напор", "issues": [{"id": "n-1", "title": "Задача", "status": "open", "priority": 1, "type": "task", "ready": True}],
        "counts": {"open": 1, "in_progress": 0}, "ready": 1, "checked_at": "сегодня", "error": None,
    }]}))
    html = client.get("/tasks?project=Напор").text
    assert "Снимок MacBook устарел" in html
    assert "Задача" in html
    assert "Проверить снимок" in html
    path.write_text("not json")
    assert beads.snapshots() == []
    assert "Доступных задач в снимке пока нет" in client.get("/tasks?full=1").text


def test_push_snapshot_uses_ssh_and_no_descriptions(monkeypatch):
    spec = importlib.util.spec_from_file_location("push_beads_snapshot", Path(__file__).parents[1] / "scripts/push_beads_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("BORT_BEADS_SYNC_HOST", "alluc@mini")
    monkeypatch.setattr(beads, "_snapshot_path", lambda: None)
    monkeypatch.setattr(beads, "snapshots", lambda **_: [{"name": "project", "issues": [{"id": "t-1", "title": "Task"}], "counts": {"open": 1}, "ready": 1, "error": None}])
    monkeypatch.setattr(module, "fetch_commands", lambda host, alias: [])
    monkeypatch.setattr(module, "sync_codex_limits", lambda host, alias: 0)
    monkeypatch.setattr(module, "sync_qwen_usage", lambda host, alias: 0)
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main() == 0
    # единственный вызов — отправка снимка (команд нет)
    assert len(calls) == 1
    assert calls[0][:4] == ["ssh", "-o", "BatchMode=yes", "-o"]


def test_snapshot_payload_has_no_descriptions(monkeypatch):
    spec = importlib.util.spec_from_file_location("push_beads_snapshot", Path(__file__).parents[1] / "scripts/push_beads_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("BORT_BEADS_SYNC_HOST", "alluc@mini")
    monkeypatch.setattr(module, "fetch_commands", lambda host, alias: [])
    monkeypatch.setattr(module, "sync_codex_limits", lambda host, alias: 0)
    monkeypatch.setattr(module, "sync_qwen_usage", lambda host, alias: 0)
    monkeypatch.setattr(beads, "_snapshot_path", lambda: None)
    monkeypatch.setattr(beads, "snapshots", lambda **_: [{"name": "project", "issues": [{"id": "t-1", "title": "Task"}], "counts": {"open": 1}, "ready": 1, "error": None}])
    seen = {}
    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["payload"] = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.main() == 0
    assert seen["args"][:4] == ["ssh", "-o", "BatchMode=yes", "-o"]
    assert seen["payload"]["projects"][0]["issues"][0] == {"id": "t-1", "title": "Task"}


def test_agent_executes_queued_commands_and_acks(monkeypatch):
    """Агент: забрал очередь, выполнил bd, отметил результат, отправил снимок."""
    spec = importlib.util.spec_from_file_location("push_beads_snapshot", Path(__file__).parents[1] / "scripts/push_beads_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(beads, "_snapshot_path", lambda: None)
    monkeypatch.setenv("BORT_BEADS_SYNC_HOST", "alluc@mini")
    commands = [
        {"id": 1, "project": "Напор", "kind": "create", "payload": {"title": "Новая", "type": "task", "priority": 2}},
        {"id": 2, "project": "*", "kind": "sync", "payload": {}},
    ]
    monkeypatch.setattr(module, "fetch_commands", lambda host, alias: commands)
    monkeypatch.setattr(module, "sync_codex_limits", lambda host, alias: 0)
    monkeypatch.setattr(module, "sync_qwen_usage", lambda host, alias: 0)
    monkeypatch.setattr(beads, "discover_projects", lambda: {"Напор": Path("/tmp/napor")})
    monkeypatch.setenv("BORT_BEADS_BIN", "/fake/bd")
    bd_calls = []
    def fake_run(args, **kwargs):
        if args[0] == "ssh":
            return subprocess.CompletedProcess(args, 0, b"", b"")
        bd_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    acked = {}
    monkeypatch.setattr(module, "ack", lambda host, alias, results: acked.setdefault("results", results))
    monkeypatch.setattr(module.beads, "snapshots", lambda **_: [{"name": "Напор", "issues": [], "counts": {}, "ready": 0, "error": None}])
    assert module.main() == 0
    assert bd_calls and bd_calls[0][:3] == ["/fake/bd", "-C", "/tmp/napor"]
    assert bd_calls[0][3:] == ["create", "--title", "Новая", "--type", "task", "-p", "2"]
    results = {item["id"]: item["status"] for item in acked["results"]}
    assert results == {1: "done", 2: "done"}


def _agent_module():
    spec = importlib.util.spec_from_file_location("push_beads_snapshot", Path(__file__).parents[1] / "scripts/push_beads_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_agent_syncs_codex_limits_on_request(monkeypatch, tmp_path):
    module = _agent_module()
    monkeypatch.setattr(module, "_ATTEMPT_STAMP", tmp_path / "attempt")
    monkeypatch.setattr(module, "codex_state", lambda host, alias: {"requested": True, "age": 5})
    monkeypatch.setattr(module.ai_providers, "codex_limits", lambda: {
        "status": "available", "captured_at": "2026-10-01T10:00:00Z", "message": None,
        "data": {"account_email": "u@e", "plan_type": "prolite", "buckets": []},
    })
    seen = {}
    monkeypatch.setattr(module, "_push_file", lambda host, alias, name, payload, **kw: seen.update(
        {"name": name, "payload": json.loads(payload)}))
    monkeypatch.setattr(module, "_clear_codex_request", lambda host, alias: seen.update({"cleared": True}))
    assert module.sync_codex_limits("mini", "") == 1
    assert seen["name"] == "codex-limits.json" and seen["cleared"] is True
    assert seen["payload"]["status"] == "available" and seen["payload"]["data"]["plan_type"] == "prolite"


def test_agent_keeps_old_codex_snapshot_on_failure(monkeypatch, tmp_path):
    module = _agent_module()
    monkeypatch.setattr(module, "_ATTEMPT_STAMP", tmp_path / "attempt")
    monkeypatch.setattr(module, "codex_state", lambda host, alias: {"requested": True, "age": 5})
    monkeypatch.setattr(module.ai_providers, "codex_limits", lambda: {
        "status": "unavailable", "captured_at": None, "message": "нет CLI", "data": None,
    })
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    seen = {}
    monkeypatch.setattr(module, "_push_file", lambda *a, **kw: seen.update({"pushed": True}))
    monkeypatch.setattr(module, "_clear_codex_request", lambda host, alias: seen.update({"cleared": True}))
    assert module.sync_codex_limits("mini", "") == 0
    assert seen == {"cleared": True}  # заявка снята, но старый снимок не перезаписан


def test_agent_skips_codex_when_fresh_and_not_requested(monkeypatch, tmp_path):
    module = _agent_module()
    monkeypatch.setattr(module, "_ATTEMPT_STAMP", tmp_path / "attempt")
    monkeypatch.setattr(module, "codex_state", lambda host, alias: {"requested": False, "age": 60})
    monkeypatch.setattr(module.ai_providers, "codex_limits", lambda: pytest.fail("CLI не должен вызываться"))
    assert module.sync_codex_limits("mini", "") == 0


def test_agent_runs_qwen_puller_on_request(monkeypatch, tmp_path):
    module = _agent_module()
    monkeypatch.setattr(module, "_QWEN_ATTEMPT_STAMP", tmp_path / "qwen-attempt")
    monkeypatch.setattr(module, "qwen_state", lambda host, alias: {"requested": True})
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "_clear_qwen_request", lambda host, alias: calls.append(("clear",)))
    assert module.sync_qwen_usage("mini", "") == 1
    assert len(calls) == 2
    args, kwargs = calls[0]
    assert args[0] == module._QWEN_DEFAULT_PYTHON
    assert args[1].endswith("pull_qwencloud_usage.py")
    assert kwargs["timeout"] == module._QWEN_PULL_TIMEOUT
    assert calls[1] == ("clear",)


def test_agent_clears_qwen_request_even_on_puller_failure(monkeypatch, tmp_path):
    module = _agent_module()
    monkeypatch.setattr(module, "_QWEN_ATTEMPT_STAMP", tmp_path / "qwen-attempt")
    monkeypatch.setattr(module, "qwen_state", lambda host, alias: {"requested": True})

    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 120)

    monkeypatch.setattr(module.subprocess, "run", boom)
    cleared = {}
    monkeypatch.setattr(module, "_clear_qwen_request", lambda host, alias: cleared.update({"ok": True}))
    assert module.sync_qwen_usage("mini", "") == 1
    assert cleared == {"ok": True}


def test_agent_skips_qwen_without_request(monkeypatch, tmp_path):
    module = _agent_module()
    monkeypatch.setattr(module, "_QWEN_ATTEMPT_STAMP", tmp_path / "qwen-attempt")
    monkeypatch.setattr(module, "qwen_state", lambda host, alias: {"requested": False})
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("сборщик не должен запускаться"))
    monkeypatch.setattr(module, "_clear_qwen_request", lambda host, alias: pytest.fail("заявку нечего снимать"))
    assert module.sync_qwen_usage("mini", "") == 0


def test_agent_respects_qwen_retry_window(monkeypatch, tmp_path):
    module = _agent_module()
    stamp = tmp_path / "qwen-attempt"
    stamp.write_text("recent", encoding="utf-8")
    monkeypatch.setattr(module, "_QWEN_ATTEMPT_STAMP", stamp)
    monkeypatch.setattr(module, "qwen_state", lambda host, alias: {"requested": True})
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("сборщик не должен запускаться"))
    assert module.sync_qwen_usage("mini", "") == 0


def test_agent_qwen_env_overrides_pull_command(monkeypatch, tmp_path):
    module = _agent_module()
    monkeypatch.setenv("BORT_QWEN_PULL_CMD", "/opt/py -X dev /tmp/pull.py")
    assert module._qwen_pull_command() == ["/opt/py", "-X", "dev", "/tmp/pull.py"]
    monkeypatch.delenv("BORT_QWEN_PULL_CMD")
    monkeypatch.setenv("BORT_QWEN_PYTHON", "/custom/python")
    assert module._qwen_pull_command()[0] == "/custom/python"
    monkeypatch.setenv("BORT_QWEN_PULL_PATH", "/opt/bin")
    assert module._qwen_pull_env()["PATH"] == "/opt/bin"
