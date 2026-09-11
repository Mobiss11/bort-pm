"""Роутер платежей по проекту (стадии оплаты)."""

from fastapi import APIRouter, Body, Depends, Response

from ...services import payments as payments_svc
from ..deps import get_conn
from . import MONEY_PAYMENT, resolve_minor, with_money

router = APIRouter(prefix="/api/v1", tags=["payments"])


@router.get("/projects/{project_id}/payments")
def list_project_payments(project_id: int, conn=Depends(get_conn)) -> dict:
    result = payments_svc.list_payments(conn, project_id)
    result["items"] = [with_money(p, MONEY_PAYMENT) for p in result["items"]]
    return result


@router.post("/projects/{project_id}/payments", status_code=201)
def add_project_payment(
    project_id: int, payload: dict = Body(...), conn=Depends(get_conn)
) -> dict:
    data = resolve_minor(payload, minor_key="amount_minor", human_key="amount")
    return with_money(payments_svc.add_payment(conn, project_id, data), MONEY_PAYMENT)


@router.delete("/payments/{payment_id}", status_code=204)
def delete_payment(payment_id: int, conn=Depends(get_conn)) -> Response:
    payments_svc.delete_payment(conn, payment_id)
    return Response(status_code=204)
