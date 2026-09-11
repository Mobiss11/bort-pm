"""Роутер сводки. Тонкий: валидация scope, вызов services.summary, деньги на границе."""

from fastapi import APIRouter, Depends

from ...services import summary as summary_svc
from ..deps import get_conn
from . import (
    MONEY_CATEGORY_TOTAL,
    MONEY_PROJECT_SUMMARY,
    MONEY_SUMMARY_ROW,
    MONEY_TOTALS,
    with_money,
)

router = APIRouter(prefix="/api/v1", tags=["summary"])


@router.get("/summary")
def get_summary(
    scope: str = "open", q: str | None = None, tasks: str | None = None, conn=Depends(get_conn)
) -> dict:
    result = summary_svc.get_summary(conn, scope=scope, q=q, tasks=tasks)
    result["totals"] = with_money(result["totals"], MONEY_TOTALS)
    result["portfolio_totals"] = with_money(result["portfolio_totals"], MONEY_TOTALS)
    result["projects"] = [with_money(p, MONEY_SUMMARY_ROW) for p in result["projects"]]
    return result


@router.get("/projects/{project_id}/summary")
def get_project_summary(project_id: int, conn=Depends(get_conn)) -> dict:
    s = with_money(summary_svc.get_project_summary(conn, project_id), MONEY_PROJECT_SUMMARY)
    s["expenses_by_category"] = [
        with_money(c, MONEY_CATEGORY_TOTAL) for c in s["expenses_by_category"]
    ]
    return s
