"""Платежи по проекту (стадии оплаты). Валюта платежа — валюта проекта, отдельного поля нет."""

from .. import errors
from ..models import PaymentCreate, validate_payload
from . import projects

KINDS = ("prepayment", "partial", "final")
KIND_LABELS = {
    "prepayment": "Предоплата",
    "partial": "Частичная оплата",
    "final": "Финальная оплата",
}


def add_payment(conn, project_id: int, data: dict) -> dict:
    projects.get_project(conn, project_id)
    payload = validate_payload(PaymentCreate, data)
    cur = conn.execute(
        "INSERT INTO payments (project_id, amount_minor, paid_on, kind, comment) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, payload.amount_minor, payload.paid_on, payload.kind, payload.comment),
    )
    conn.commit()
    return get_payment(conn, cur.lastrowid)


def get_payment(conn, payment_id: int) -> dict:
    row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        raise errors.NotFound(
            f"Платёж {payment_id} не найден", details={"payment_id": payment_id}
        )
    return dict(row)


def list_payments(conn, project_id: int) -> dict:
    projects.get_project(conn, project_id)
    rows = conn.execute(
        "SELECT * FROM payments WHERE project_id = ? ORDER BY paid_on DESC, id DESC",
        (project_id,),
    ).fetchall()
    items = [dict(r) for r in rows]
    return {"items": items, "total_minor": sum(p["amount_minor"] for p in items)}


def delete_payment(conn, payment_id: int) -> None:
    get_payment(conn, payment_id)
    conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
    conn.commit()
