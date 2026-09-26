"""Регрессии финального ревью: safe return, truthful attention, поиск проекта,
edit/delete HTML-флоу.

Все CRUD — на fixture-БД (tests/conftest.py, BORT_DB=tmp). Production не трогаем.
"""

import re
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from bort.services import projects, summary, tasks as tasks_svc
from bort.web.pages import _safe_return_path

TODAY = "2026-09-14"


# ---------- 1. safe return path ----------


def test_safe_return_path_preserves_query_and_whitelists():
    assert _safe_return_path("/?q=alpha&tasks=open") == "/?q=alpha&tasks=open"
    # единственный разрешённый путь — сводка; прочее схлопывается в '/'
    assert _safe_return_path("/tasks?view=list&q=alpha") == "/"
    assert _safe_return_path("/tasks-invalid") == "/"
    assert _safe_return_path("/etc/passwd") == "/"
    assert _safe_return_path("/tasks/../etc") == "/"
    assert _safe_return_path("//evil.example") == "/"
    assert _safe_return_path("https://evil.example/x") == "/"
    assert _safe_return_path("/tasks\\evil") == "/"
    assert _safe_return_path("/tasks?q=a\\b") == "/"
    assert _safe_return_path("/\x00tasks") == "/"
    assert _safe_return_path("/tasks\n") == "/"
    assert _safe_return_path(None) == "/"
    assert _safe_return_path("") == "/"
    assert _safe_return_path("/tasks?") == "/"
    # уже закодированные значения не перекодируются
    assert _safe_return_path("/?q=%26%23+&x=1") == "/?q=%26%23+&x=1"


SPECIAL_NAMES = ["Спец& #+ \"кир", "a&b", "a#b", "a+b", "a\"b", "кириллица"]


@pytest.mark.parametrize("name", SPECIAL_NAMES)
def test_summary_links_encode_return_to_with_special_query(client, name):
    p = client.post("/api/v1/projects", json={"name": name, "status": "active"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "задача"})
    html = client.get("/", params={"q": name, "tasks": "open"}).text
    tokens = re.findall(r'href="/projects/\d+\?return_to=([^"&]+)"', html)
    assert tokens, "должны быть ссылки с return_to"
    for token in tokens:
        inner = unquote(token)
        assert _safe_return_path(inner) == inner, inner
        qs = parse_qs(urlsplit(inner).query)
        assert qs.get("q", [""])[0] == name
        assert qs.get("tasks", [""])[0] == "open"


def test_project_back_link_preserves_filtered_summary(client):
    p = client.post("/api/v1/projects", json={"name": "Возврат", "status": "active"}).json()
    r = client.get(f"/projects/{p['id']}", params={"return_to": "/?q=alpha&tasks=open"})
    assert 'href="/?q=alpha&amp;tasks=open"' in r.text


def test_project_back_link_rejects_invalid(client):
    p = client.post("/api/v1/projects", json={"name": "Возврат2", "status": "active"}).json()
    for bad in ["https://evil.example", "//evil.example", "/tasks-invalid", "/etc/passwd"]:
        r = client.get(f"/projects/{p['id']}", params={"return_to": bad})
        assert "evil.example" not in r.text
        assert "/etc/passwd" not in r.text


# ---------- 2. truthful attention ----------


def _p(conn, name="P", **extra):
    data = {"name": name, "status": "active"}
    data.update(extra)
    return projects.create_project(conn, data)


def _t(conn, pid, title, **extra):
    data = {"title": title}
    data.update(extra)
    return tasks_svc.create_task(conn, pid, data)


def test_overdue_task_surfaces_even_when_other_is_next(conn):
    p = _p(conn, "Конкурирующие")  # без дедлайна проекта
    _t(conn, p["id"], "ALPHA", priority=2)  # недатированная todo
    _t(conn, p["id"], "BETA", priority=3, status="in_progress", deadline="2020-01-01")
    att = summary.get_attention(conn, today=TODAY)
    by_title = {i["task"]["title"]: i for i in att["items"]}
    assert set(by_title) == {"ALPHA", "BETA"}
    assert by_title["BETA"]["bucket"] == "overdue"
    assert by_title["BETA"]["task_deadline_state"] == "overdue"
    assert by_title["ALPHA"]["bucket"] in ("urgent", "started", "other")


def test_priority1_undated_wins_next_but_overdue_still_surfaces(conn):
    p = _p(conn, "П1")
    _t(conn, p["id"], "P1-UNDATED", priority=1)
    _t(conn, p["id"], "OVD", priority=3, deadline="2020-01-01")
    att = summary.get_attention(conn, today=TODAY)
    titles = [i["task"]["title"] for i in att["items"]]
    assert set(titles) == {"P1-UNDATED", "OVD"}
    assert att["items"][0]["bucket"] == "overdue"  # просрочка всегда сверху
    buckets = [(i["task"]["title"], i["bucket"]) for i in att["items"]]
    assert buckets == [("OVD", "overdue"), ("P1-UNDATED", "urgent")]


def test_overdue_todo_vs_started_both_surface(conn):
    p = _p(conn, "Просрочники")
    _t(conn, p["id"], "OVD-TODO", priority=2, deadline="2020-01-01")
    _t(conn, p["id"], "OVD-STARTED", priority=3, status="in_progress", deadline="2020-01-02")
    att = summary.get_attention(conn, today=TODAY)
    titles = [i["task"]["title"] for i in att["items"]]
    assert set(titles) == {"OVD-TODO", "OVD-STARTED"}
    assert all(i["bucket"] == "overdue" for i in att["items"])
    # крупные просрочки первыми (deadline asc), затем приоритет
    assert titles[0] == "OVD-TODO"


def test_bucket_ordering_overdue_urgent_started_other(conn):
    a = _p(conn, "A")
    _t(conn, a["id"], "OVD", priority=4, deadline="2020-01-01")
    b = _p(conn, "B")
    _t(conn, b["id"], "URG", priority=1)
    c = _p(conn, "C")
    _t(conn, c["id"], "START", priority=4, status="in_progress")
    d = _p(conn, "D")
    _t(conn, d["id"], "OTHER", priority=4)
    att = summary.get_attention(conn, today=TODAY)
    assert [i["bucket"] for i in att["items"]] == ["overdue", "urgent", "started", "other"]


def test_closed_project_overdue_task_excluded(conn):
    p = _p(conn, "Закрытый", status="closed")
    _t(conn, p["id"], "CLOSED-OVD", priority=3, status="in_progress", deadline="2020-01-01")
    att = summary.get_attention(conn, today=TODAY)
    titles = [i["task"]["title"] for i in att["items"]]
    assert "CLOSED-OVD" not in titles
    assert all(x["name"] != "Закрытый" for x in att["overdue"])
    assert all(x["name"] != "Закрытый" for x in att["no_tasks"])


def test_attention_page_surfaces_overdue_task(client):
    p = client.post("/api/v1/projects", json={"name": "Страница", "status": "active"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "ALPHA", "priority": 2})
    client.post(
        f"/api/v1/projects/{p['id']}/tasks",
        json={"title": "BETA", "priority": 3, "status": "in_progress", "deadline": "2020-01-01"},
    )
    html = client.get("/").text
    panel = html.split('id="attention-panel"', 1)[1].split("</section>", 1)[0]
    assert "ALPHA" in panel and "BETA" in panel
    assert "Просрочено" in panel


# ---------- 3. project task search ----------


def test_project_task_search_filters_list_and_kanban(client):
    p = client.post("/api/v1/projects", json={"name": "Поиск", "status": "active"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "ALPHA"})
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "BETA"})
    html = client.get(f"/ui/projects/{p['id']}/tasks", params={"q": "ALPHA"}).text
    assert "ALPHA" in html
    assert "BETA" not in html
    assert html.count('id="kanban-block"') == 1
    assert "hx-swap-oob" in html


def test_project_task_search_accepts_legacy_task_q(client):
    p = client.post("/api/v1/projects", json={"name": "Поиск2", "status": "active"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "ALPHA"})
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "BETA"})
    html = client.get(f"/ui/projects/{p['id']}/tasks", params={"task-q": "ALPHA"}).text
    assert "ALPHA" in html
    assert "BETA" not in html


def test_project_tasks_block_sends_q_parameter(client):
    p = client.post("/api/v1/projects", json={"name": "Параметр", "status": "active"}).json()
    html = client.get(f"/projects/{p['id']}").text
    assert 'class="task-search" name="q"' in html
    assert 'name="task-q"' not in html


# ---------- 4. edit / delete HTML flows ----------


def test_task_edit_html_flow_preserves_filters(client):
    p = client.post("/api/v1/projects", json={"name": "Правка", "status": "active"}).json()
    t1 = client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "ALPHA"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "BETA"})
    r = client.get(f"/ui/tasks/{t1['id']}/edit", params={"q": "ALPHA", "view": "list", "project_id": p["id"]})
    assert r.status_code == 200
    assert 'name="title"' in r.text and "ALPHA" in r.text
    r = client.post(
        f"/ui/tasks/{t1['id']}/edit",
        data={"title": "ALPHA-2", "priority": "1", "deadline": "", "status": "todo",
              "q": "ALPHA", "view": "list", "project_id": str(p["id"])},
    )
    assert r.status_code == 200
    assert "ALPHA-2" in r.text
    assert "BETA" not in r.text  # q-фильтр сохранён после update
    assert client.get(f"/api/v1/tasks/{t1['id']}").json()["title"] == "ALPHA-2"


def test_task_delete_project_list_preserves_view(client):
    p = client.post("/api/v1/projects", json={"name": "УдалениеП", "status": "active"}).json()
    t1 = client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "ALPHA"}).json()
    r = client.delete(f"/ui/tasks/{t1['id']}", params={"view": "list", "q": "", "project_id": p["id"]})
    assert r.status_code == 200
    assert 'id="tasks-block"' in r.text
    assert "ALPHA" not in r.text


def test_task_disclosure_controls_present(client):
    p = client.post("/api/v1/projects", json={"name": "Дисклоуз", "status": "active"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "ALPHA"})
    html = client.get(f"/projects/{p['id']}").text
    assert f'/ui/tasks/' in html
    assert 'hx-delete="/ui/tasks/' in html  # удаление за раскрытием


# ---------- 5. OOB-синхронизация состояния сводки ----------


def test_summary_filter_response_updates_state_oob(client):
    p = client.post("/api/v1/projects", json={"name": "СводХром", "status": "active"}).json()
    client.post(f"/api/v1/projects/{p['id']}/tasks", json={"title": "T"})
    r = client.get("/ui/projects", params={"tasks": "open", "q": "Свод"})
    assert 'id="summary-filter-state"' in r.text
    assert "hx-swap-oob" in r.text
    assert "с открытыми задачами" in r.text
    assert "поиск «Свод»" in r.text
    assert "return_to=" in r.text  # attention back links пересобраны под текущие фильтры
