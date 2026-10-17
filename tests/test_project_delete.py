"""UI-удаление проекта: htmx-ответ, редирект, return_to, 404 и каскады."""

import os
import re

import pytest

from bort import db


def _mk_project(client, name="Удаляемый", **extra):
    payload = {"name": name, "status": "active"}
    payload.update(extra)
    r = client.post("/api/v1/projects", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _db_conn():
    return db.connect(os.environ["BORT_DB"])


def test_ui_delete_htmx_returns_redirect_and_removes_project(client):
    p = _mk_project(client)
    r = client.delete(f"/ui/projects/{p['id']}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.text == ""
    assert r.headers["HX-Redirect"] == "/"
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404


@pytest.mark.parametrize("via_form", [False, True], ids=["query", "form"])
def test_ui_delete_preserves_return_to(client, via_form):
    p = _mk_project(client, name="СФильтром")
    url = f"/ui/projects/{p['id']}"
    headers = {"HX-Request": "true"}
    if via_form:
        # так шлёт htmx: DELETE с form-encoded телом (hx-vals)
        r = client.request("DELETE", url, headers=headers, data={"return_to": "/?view=kanban"})
    else:
        r = client.delete(url, headers=headers, params={"return_to": "/?view=kanban"})
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/?view=kanban"
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404


def test_ui_delete_without_htmx_redirects_303(client):
    p = _mk_project(client, name="БезHtmx")
    r = client.delete(
        f"/ui/projects/{p['id']}", params={"return_to": "/?view=kanban"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["Location"] == "/?view=kanban"
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404


def test_ui_delete_rejects_external_return_to(client):
    p = _mk_project(client, name="OpenRedirect")
    r = client.delete(
        f"/ui/projects/{p['id']}",
        headers={"HX-Request": "true"},
        params={"return_to": "//evil.example/x"},
    )
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/"


def test_ui_delete_missing_project_404(client):
    r = client.delete("/ui/projects/999999", headers={"HX-Request": "true"})
    assert r.status_code == 404


def test_project_page_has_delete_button(client):
    p = _mk_project(client, name="КнопкаДел")
    pid = p["id"]
    html = client.get(f"/projects/{pid}").text
    assert f'hx-delete="/ui/projects/{pid}"' in html
    assert "hx-confirm=" in html
    assert "Удалить проект «КнопкаДел»?" in html
    assert "Задачи, затраты, оплаты и связи проекта будут удалены безвозвратно." in html
    assert 'class="btn btn-small link-danger"' in html  # стиль из app.css, новый CSS не нужен
    # именно <button>, без обёртки <details>: клавиатура работает из коробки
    assert re.search(r'<button[^>]*hx-delete="/ui/projects/%d"' % pid, html)


def test_ui_delete_cascades_tasks_expenses_payments(client):
    p = _mk_project(client, name="Каскад")
    pid = p["id"]
    task = client.post(f"/api/v1/projects/{pid}/tasks", json={"title": "Задача каскада"})
    assert task.status_code == 201, task.text
    expense = client.post(
        f"/api/v1/projects/{pid}/expenses",
        json={"amount": "1 000", "spent_on": "2026-09-01", "category_code": "contractors"},
    )
    assert expense.status_code == 201, expense.text
    payment = client.post(
        f"/api/v1/projects/{pid}/payments",
        json={"amount": "500", "paid_on": "2026-09-01", "kind": "prepayment"},
    )
    assert payment.status_code == 201, payment.text

    r = client.delete(f"/ui/projects/{pid}", headers={"HX-Request": "true"})
    assert r.status_code == 200

    conn = _db_conn()
    try:
        for table in ("tasks", "expenses", "payments"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, f"{table}: осталось {count} строк"
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    finally:
        conn.close()
