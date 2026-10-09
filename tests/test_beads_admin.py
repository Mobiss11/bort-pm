"""Команды к Beads из интерфейса: очередь, выполнение, скрытые проекты."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from bort import errors
from bort.services import beads, beads_admin


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Каталоги с фейковым bd: чтение отдаёт старую задачу, запись — успех."""
    root = tmp_path / "work"
    for name in ("Напор", "second"):
        marker = root / name / ".beads"
        marker.mkdir(parents=True)
        (marker / "config.yaml").write_text("issue-prefix: t\n")
    monkeypatch.setenv("BORT_BEADS_ROOTS", str(root))
    monkeypatch.setenv("BORT_BEADS_BIN", "/fake/bd")
    beads._cache.clear()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "list" in args:
            payload = [{"id": "t-1", "title": "Старая", "status": "open", "priority": 2, "issue_type": "task"}]
        elif "ready" in args:
            payload = []
        else:
            payload = []
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    monkeypatch.setattr(beads.subprocess, "run", run)
    yield {"root": root, "calls": calls}
    beads._cache.clear()


def _project(name):
    return {"name": name, "issues": [{"id": "t-1", "title": "Старая", "status": "open", "priority": 2, "type": "task", "ready": False}],
            "counts": {"open": 1, "in_progress": 0}, "ready": 0, "checked_at": "сегодня", "error": None}


# --- Сервис: очередь и валидация ---


def test_enqueue_validates_and_deduplicates(conn):
    item = beads_admin.enqueue(conn, project="Напор", kind="create",
                               payload={"title": " Новая ", "type": "bug", "priority": 1})
    assert item["status"] == "pending"
    assert item["payload"] == {"title": "Новая", "type": "bug", "priority": 1, "description": None}
    with pytest.raises(errors.Conflict):
        beads_admin.enqueue(conn, project="Напор", kind="create",
                            payload={"title": "Новая", "type": "bug", "priority": 1})
    with pytest.raises(errors.ValidationError):
        beads_admin.enqueue(conn, project="Напор", kind="create", payload={"title": ""})
    with pytest.raises(errors.ValidationError):
        beads_admin.enqueue(conn, project="Напор", kind="create", payload={"title": "x", "type": "нет-такого"})
    with pytest.raises(errors.ValidationError):
        beads_admin.enqueue(conn, project="Напор", kind="update", payload={"issue": "t-1"})
    with pytest.raises(errors.ValidationError):
        beads_admin.enqueue(conn, project="Напор", kind="update", payload={"issue": "t-1", "status": "closed"})
    with pytest.raises(errors.ValidationError):
        beads_admin.enqueue(conn, project="Напор", kind="нет", payload={})
    with pytest.raises(errors.ValidationError):
        beads_admin.enqueue(conn, project="нет-проекта", kind="create",
                            payload={"title": "x"}, available={"Напор"})


def test_sync_command_allows_empty_project(conn):
    item = beads_admin.enqueue(conn, project="", kind="sync", payload={})
    assert item["project"] == beads_admin.SYNC_PROJECT
    assert item["payload"] == {}


def test_mark_and_dismiss(conn):
    first = beads_admin.enqueue(conn, project="Напор", kind="close", payload={"issue": "t-1"})
    second = beads_admin.enqueue(conn, project="Напор", kind="reopen", payload={"issue": "t-2"})
    with pytest.raises(errors.ValidationError):
        beads_admin.dismiss(conn, first["id"])  # ещё pending
    beads_admin.mark(conn, [{"id": first["id"], "status": "error", "error": "bd упал"}])
    beads_admin.mark(conn, [{"id": second["id"], "status": "done"}])
    listing = beads_admin.list_commands(conn)
    assert [item["id"] for item in listing["pending"]] == []
    assert listing["errors"][0]["error"] == "bd упал"
    beads_admin.dismiss(conn, first["id"])
    with pytest.raises(errors.NotFound):
        beads_admin.dismiss(conn, first["id"])


# --- Сервис: выполнение ---


def test_execute_commands_builds_args_and_reports(conn, workspace):
    commands = [
        {"id": 1, "project": "Напор", "kind": "create", "payload": beads_admin._normalize("create", {"title": "T", "type": "bug", "priority": 1})},
        {"id": 2, "project": "Напор", "kind": "update", "payload": {"issue": "t-1", "status": "in_progress"}},
        {"id": 3, "project": "Напор", "kind": "close", "payload": {"issue": "t-1", "reason": None}},
        {"id": 4, "project": "Напор", "kind": "reopen", "payload": {"issue": "t-1"}},
        {"id": 5, "project": "*", "kind": "sync", "payload": {}},
        {"id": 6, "project": "пропал", "kind": "reopen", "payload": {"issue": "t-1"}},
    ]
    dirs = beads.discover_projects()
    results = beads_admin.execute_commands(commands, dirs, "/fake/bd")
    statuses = {item["id"]: item["status"] for item in results}
    assert statuses == {1: "done", 2: "done", 3: "done", 4: "done", 5: "done", 6: "error"}
    assert results[-1]["error"] == "Проект не найден на машине с bd"
    write_calls = [call for call in workspace["calls"] if "list" not in call and "ready" not in call]
    assert write_calls[0][3:] == ["create", "--title", "T", "--type", "bug", "-p", "1"]
    assert write_calls[1][3:] == ["update", "t-1", "--status", "in_progress"]
    assert write_calls[2][3:] == ["close", "t-1"]
    assert write_calls[3][3:] == ["reopen", "t-1"]
    assert len(write_calls) == 4  # sync и «пропал» без вызова bd


def test_execute_reports_bd_failure(conn, workspace, monkeypatch):
    def fail(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "Error: boom")
    monkeypatch.setattr(beads.subprocess, "run", fail)
    results = beads_admin.execute_commands(
        [{"id": 9, "project": "Напор", "kind": "reopen", "payload": {"issue": "t-1"}}],
        beads.discover_projects(), "/fake/bd",
    )
    assert results == [{"id": 9, "status": "error", "error": "Error: boom"}]


def test_no_binary_marks_error(conn, workspace):
    results = beads_admin.execute_commands(
        [{"id": 4, "project": "Напор", "kind": "reopen", "payload": {"issue": "t-1"}}],
        beads.discover_projects(), None,
    )
    assert results[0]["status"] == "error" and "bd не найден" in results[0]["error"]


def test_process_pending_locally_executes_and_marks(conn, workspace):
    beads_admin.enqueue(conn, project="Напор", kind="create", payload={"title": "Локальная"})
    assert beads_admin.process_pending_locally(conn) == 1
    assert beads_admin.list_commands(conn)["pending"] == []
    done = conn.execute("SELECT status, processed_at FROM beads_commands").fetchone()
    assert done["status"] == "done" and done["processed_at"]


# --- Скрытые проекты ---


def test_hide_and_unhide_projects(client, workspace):
    page = client.get("/tasks?full=1").text
    assert "Напор" in page and "second" in page
    response = client.post("/tasks/projects/hide", data={"name": "second", "project": "", "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban"})
    assert response.status_code == 200
    assert '<span class="beads-project-name">second</span>' not in response.text
    assert "Скрытые · 1" in response.text
    hidden_list = re.search(r'<details class="beads-hidden".*?</details>', response.text, re.S).group(0)
    assert "second" in hidden_list  # проект не потерялся, его можно вернуть
    response = client.post("/tasks/projects/unhide", data={"name": "second", "project": "", "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban"})
    assert response.status_code == 200
    assert '<span class="beads-project-name">second</span>' in response.text
    assert "Скрытые · 1" not in response.text


def test_hide_selected_project_shows_overview(client, workspace):
    page = client.get("/tasks?project=second").text
    assert "Старая" in page  # задачи проекта открыты
    response = client.post("/tasks/projects/hide", data={"name": "second", "project": "second", "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban"})
    assert response.status_code == 200
    assert "Выберите проект" in response.text  # скрытый выбранный проект сбрасывает сцену


def test_hidden_project_excluded_from_link_select(client, workspace):
    pid = client.post("/api/v1/projects", json={"name": "Сводный"}).json()["id"]
    assert "second" in client.get(f"/tasks?link_project_id={pid}").text
    client.post("/tasks/projects/hide", data={"name": "second", "project": "", "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban"})
    assert '<option value="second"' not in client.get(f"/tasks?link_project_id={pid}").text
    assert '<option value="second" disabled' not in client.get(f"/tasks?link_project_id={pid}").text


# --- UI: создание и управление задачами ---


def test_ui_create_task_snapshot_mode_queues_and_marks_pending(client, tmp_path, monkeypatch):
    path = tmp_path / "beads-snapshot.json"
    monkeypatch.setenv("BORT_BEADS_SNAPSHOT", str(path))
    monkeypatch.setattr(beads, "subprocess", type("X", (), {"run": staticmethod(lambda *a, **k: pytest.fail("bd must not run on mini"))}))
    path.write_text(json.dumps({"version": 1, "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                                "projects": [_project("Напор")]}))
    response = client.post("/tasks/issues/create", data={
        "project": "Напор", "task_title": "Новая с снимка", "task_type": "bug", "task_priority": "1",
        "task_description": "", "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban",
    })
    assert response.status_code == 200
    assert "Отправлено и ждёт MacBook: 1" in response.text
    assert "Новая задача «Новая с снимка» (bug, P1)" in response.text
    assert "ждём MacBook" in response.text
    assert 'hx-trigger="every 20s"' in response.text  # автообновление
    # карандаш на панели не остался в HTML как pending-индикатор задачи
    assert "↻ отправлено" not in response.text


def test_ui_create_task_local_mode_executes_now(client, workspace):
    response = client.post("/tasks/issues/create", data={
        "project": "Напор", "task_title": "Локальная задача", "task_type": "task", "task_priority": "2",
        "task_description": "", "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban",
    })
    assert response.status_code == 200
    write_calls = [call for call in workspace["calls"] if "list" not in call and "ready" not in call]
    assert write_calls[0][3:] == ["create", "--title", "Локальная задача", "--type", "task", "-p", "2"]
    assert "ждём MacBook" not in response.text


def test_ui_create_validation_error_opens_form(client, workspace):
    response = client.post("/tasks/issues/create", data={
        "project": "Напор", "task_title": "   ", "task_type": "task", "task_priority": "2",
        "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban",
    })
    assert response.status_code == 422
    assert "«Название» обязательно" in response.text
    assert re.search(r'<details class="beads-new-task"[^>]* open>', response.text)
    assert "ждём MacBook" not in response.text


def test_ui_update_and_close_from_issue_card(client, workspace):
    response = client.post("/tasks/issues/update", data={
        "project": "Напор", "issue": "t-1", "issue_status": "in_progress", "issue_priority": "1",
        "status": "active", "priority": "all", "type": "all", "q": "", "view": "list",
    })
    assert response.status_code == 200
    writes = [call for call in workspace["calls"] if "list" not in call and "ready" not in call]
    assert writes[0][3:] == ["update", "t-1", "--status", "in_progress", "-p", "1"]
    response = client.post("/tasks/issues/close", data={
        "project": "Напор", "issue": "t-1", "status": "active", "priority": "all", "type": "all", "q": "", "view": "list",
    })
    assert response.status_code == 200
    writes = [call for call in workspace["calls"] if "list" not in call and "ready" not in call]
    assert writes[-1][3:] == ["close", "t-1"]
    # канбан и карточка задачи содержат управление и кнопку закрытия
    page = client.get("/tasks?project=Напор").text
    assert 'name="issue_status"' in page and 'hx-post="/tasks/issues/close"' in page


def test_ui_create_unknown_project_is_422_with_message(client, workspace):
    response = client.post("/tasks/issues/create", data={
        "project": "НетТакого", "task_title": "Задача", "task_type": "task", "task_priority": "2",
        "status": "active", "priority": "all", "type": "all", "q": "", "view": "kanban",
    })
    assert response.status_code == 422
    assert "Проект задач не найден среди доступных" in response.text


def test_ui_issue_form_carries_state_and_hidden_project_restores(client, workspace):
    page = client.get("/tasks?project=Напор&view=list").text
    assert 'name="project" value="Напор"' in page
    assert 'name="view" value="list"' in page
    # синхронизация в локальном режиме = обычное обновление без очереди
    response = client.post("/tasks/sync", data={"project": "Напор", "status": "active", "priority": "all", "type": "all", "q": "", "view": "list"})
    assert response.status_code == 200
    assert "ждём MacBook" not in response.text
