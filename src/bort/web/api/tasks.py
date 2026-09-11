"""Роутер задач."""

from fastapi import APIRouter, Body, Depends, Response

from ... import errors
from ...services import tasks as tasks_svc
from ..deps import get_conn

router = APIRouter(prefix="/api/v1", tags=["tasks"])


@router.get("/projects/{project_id}/tasks")
def list_project_tasks(
    project_id: int, status: str | None = None, conn=Depends(get_conn)
) -> dict:
    return {"items": tasks_svc.list_tasks(conn, project_id, status=status)}


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_project_task(
    project_id: int, payload: dict = Body(...), conn=Depends(get_conn)
) -> dict:
    return tasks_svc.create_task(conn, project_id, payload)


@router.get("/tasks/{task_id}")
def get_task(task_id: int, conn=Depends(get_conn)) -> dict:
    return tasks_svc.get_task(conn, task_id)


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: dict = Body(default={}), conn=Depends(get_conn)) -> dict:
    return tasks_svc.update_task(conn, task_id, payload)


@router.post("/tasks/{task_id}/close")
def close_task(task_id: int, payload: dict = Body(default={}), conn=Depends(get_conn)) -> dict:
    outcome = payload.get("status")
    if outcome is None:
        raise errors.ValidationError(
            "Ожидается тело {\"status\": \"done\"|\"cancelled\"}", details={"status": None}
        )
    return tasks_svc.close_task(conn, task_id, outcome)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, conn=Depends(get_conn)) -> Response:
    tasks_svc.delete_task(conn, task_id)
    return Response(status_code=204)
