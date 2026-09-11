"""Затраты. В v1 валюта затраты обязана совпадать с валютой проекта (см. §9)."""

import sqlite3

from .. import dates, errors
from ..models import CategoryCreate, ExpenseCreate, ExpenseUpdate, validate_payload
from . import projects


def _check_category(conn, code: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM expense_categories WHERE code = ?", (code,)
    ).fetchone()
    if row is None:
        raise errors.ValidationError(
            f"Неизвестная категория затрат: {code}", details={"category_code": code}
        )


def _check_currency(project: dict, currency: str) -> None:
    if currency != project["currency"]:
        raise errors.ValidationError(
            f"Валюта затраты ({currency}) должна совпадать с валютой проекта "
            f"({project['currency']})",
            details={"expense_currency": currency, "project_currency": project["currency"]},
        )


def add_expense(conn, project_id: int, data: dict) -> dict:
    project = projects.get_project(conn, project_id)
    payload = validate_payload(ExpenseCreate, data)
    _check_currency(project, payload.currency)
    _check_category(conn, payload.category_code)

    cur = conn.execute(
        """
        INSERT INTO expenses
            (project_id, amount_minor, currency, spent_on, category_code, comment)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            project_id,
            payload.amount_minor,
            payload.currency,
            payload.spent_on,
            payload.category_code,
            payload.comment,
        ),
    )
    conn.commit()
    return get_expense(conn, cur.lastrowid)


def get_expense(conn, expense_id: int) -> dict:
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        raise errors.NotFound(
            f"Затрата {expense_id} не найдена", details={"expense_id": expense_id}
        )
    return dict(row)


def list_expenses(
    conn,
    project_id: int,
    *,
    category_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    projects.get_project(conn, project_id)

    clauses = ["project_id = ?"]
    params: list = [project_id]
    if category_code is not None:
        clauses.append("category_code = ?")
        params.append(category_code)
    if date_from is not None:
        try:
            dates.parse_date(date_from)
        except ValueError as e:
            raise errors.ValidationError(str(e), details={"date_from": date_from}) from e
        clauses.append("spent_on >= ?")
        params.append(date_from)
    if date_to is not None:
        try:
            dates.parse_date(date_to)
        except ValueError as e:
            raise errors.ValidationError(str(e), details={"date_to": date_to}) from e
        clauses.append("spent_on <= ?")
        params.append(date_to)

    rows = conn.execute(
        f"SELECT * FROM expenses WHERE {' AND '.join(clauses)} "
        "ORDER BY spent_on DESC, id DESC",
        params,
    ).fetchall()
    items = [dict(r) for r in rows]
    return {"items": items, "total_minor": sum(e["amount_minor"] for e in items)}


def update_expense(conn, expense_id: int, data: dict) -> dict:
    current = get_expense(conn, expense_id)
    project = projects.get_project(conn, current["project_id"])
    fields = validate_payload(ExpenseUpdate, data).model_dump(exclude_unset=True)
    if not fields:
        return current

    if "currency" in fields:
        _check_currency(project, fields["currency"])
    if "category_code" in fields:
        _check_category(conn, fields["category_code"])

    cols = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE expenses SET {cols} WHERE id = ?",
        [*fields.values(), expense_id],
    )
    conn.commit()
    return get_expense(conn, expense_id)


def delete_expense(conn, expense_id: int) -> None:
    get_expense(conn, expense_id)
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()


# --- Справочник категорий ---


def list_categories(conn, *, include_inactive: bool = False) -> dict:
    where = "" if include_inactive else "WHERE is_active = 1"
    rows = conn.execute(
        f"SELECT * FROM expense_categories {where} ORDER BY sort_order ASC, code ASC"
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


def create_category(conn, data: dict) -> dict:
    payload = validate_payload(CategoryCreate, data)
    try:
        conn.execute(
            "INSERT INTO expense_categories (code, title_ru, sort_order) VALUES (?, ?, ?)",
            (payload.code, payload.title_ru, payload.sort_order),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            f"Категория '{payload.code}' уже существует",
            details={"code": payload.code},
        ) from e
    row = conn.execute(
        "SELECT * FROM expense_categories WHERE code = ?", (payload.code,)
    ).fetchone()
    return dict(row)
