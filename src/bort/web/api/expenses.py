"""Роутер затрат и справочника категорий."""

from fastapi import APIRouter, Body, Depends, Response

from ...services import expenses as expenses_svc
from ..deps import get_conn
from . import MONEY_EXPENSE, resolve_minor, with_money

router = APIRouter(prefix="/api/v1", tags=["expenses"])


@router.get("/projects/{project_id}/expenses")
def list_project_expenses(
    project_id: int,
    category_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    conn=Depends(get_conn),
) -> dict:
    result = expenses_svc.list_expenses(
        conn,
        project_id,
        category_code=category_code,
        date_from=date_from,
        date_to=date_to,
    )
    result["items"] = [with_money(e, MONEY_EXPENSE) for e in result["items"]]
    return result


@router.post("/projects/{project_id}/expenses", status_code=201)
def add_project_expense(
    project_id: int, payload: dict = Body(...), conn=Depends(get_conn)
) -> dict:
    data = resolve_minor(payload, minor_key="amount_minor", human_key="amount")
    return with_money(expenses_svc.add_expense(conn, project_id, data), MONEY_EXPENSE)


@router.patch("/expenses/{expense_id}")
def update_expense(
    expense_id: int, payload: dict = Body(default={}), conn=Depends(get_conn)
) -> dict:
    data = resolve_minor(payload, minor_key="amount_minor", human_key="amount")
    return with_money(expenses_svc.update_expense(conn, expense_id, data), MONEY_EXPENSE)


@router.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: int, conn=Depends(get_conn)) -> Response:
    expenses_svc.delete_expense(conn, expense_id)
    return Response(status_code=204)


@router.get("/expense-categories")
def list_categories(conn=Depends(get_conn)) -> dict:
    return expenses_svc.list_categories(conn)


@router.post("/expense-categories", status_code=201)
def create_category(payload: dict = Body(...), conn=Depends(get_conn)) -> dict:
    return expenses_svc.create_category(conn, payload)
