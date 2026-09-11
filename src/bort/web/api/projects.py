"""Роутер проектов. Логика — в services.projects; здесь только транспорт."""

from fastapi import APIRouter, Body, Depends, Response

from ...services import chats as chats_svc
from ...services import expenses as expenses_svc
from ...services import people as people_svc
from ...services import projects as projects_svc
from ...services import tasks as tasks_svc
from ..deps import get_conn
from . import MONEY_EXPENSE, MONEY_PROJECT, resolve_minor, with_money

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("")
def list_projects(
    status: str | None = None,
    priority: int | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort: str | None = None,
    conn=Depends(get_conn),
) -> dict:
    result = projects_svc.list_projects(
        conn, status=status, priority=priority, q=q, limit=limit, offset=offset, sort=sort
    )
    result["items"] = [with_money(p, MONEY_PROJECT) for p in result["items"]]
    return result


@router.post("", status_code=201)
def create_project(payload: dict = Body(...), conn=Depends(get_conn)) -> dict:
    data = resolve_minor(payload, minor_key="deal_amount_minor", human_key="deal_amount")
    return with_money(projects_svc.create_project(conn, data), MONEY_PROJECT)


@router.get("/{project_id}")
def get_project(project_id: int, conn=Depends(get_conn)) -> dict:
    project = with_money(projects_svc.get_project(conn, project_id), MONEY_PROJECT)
    listing = expenses_svc.list_expenses(conn, project_id)
    project["tasks"] = tasks_svc.list_tasks(conn, project_id)
    project["expenses"] = {
        "items": [with_money(e, MONEY_EXPENSE) for e in listing["items"]],
        "total_minor": listing["total_minor"],
    }
    project["chats"] = chats_svc.list_project_chats(conn, project_id)["items"]
    project["people"] = people_svc.list_project_people(conn, project_id)["items"]
    return project


@router.patch("/{project_id}")
def update_project(
    project_id: int, payload: dict = Body(default={}), conn=Depends(get_conn)
) -> dict:
    data = resolve_minor(payload, minor_key="deal_amount_minor", human_key="deal_amount")
    return with_money(projects_svc.update_project(conn, project_id, data), MONEY_PROJECT)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, conn=Depends(get_conn)) -> Response:
    projects_svc.delete_project(conn, project_id)
    return Response(status_code=204)
