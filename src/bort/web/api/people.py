"""Роутер людей: справочник и привязки к проекту."""

from fastapi import APIRouter, Body, Depends, Response

from ...services import people as people_svc
from ..deps import get_conn

router = APIRouter(prefix="/api/v1", tags=["people"])


@router.get("/people")
def list_people(q: str | None = None, conn=Depends(get_conn)) -> dict:
    return people_svc.list_people(conn, q=q)


@router.post("/people", status_code=201)
def create_person(payload: dict = Body(...), conn=Depends(get_conn)) -> dict:
    return people_svc.create_person(conn, payload)


@router.get("/people/{person_id}")
def get_person(person_id: int, conn=Depends(get_conn)) -> dict:
    return people_svc.get_person(conn, person_id)


@router.patch("/people/{person_id}")
def update_person(
    person_id: int, payload: dict = Body(default={}), conn=Depends(get_conn)
) -> dict:
    return people_svc.update_person(conn, person_id, payload)


@router.delete("/people/{person_id}", status_code=204)
def delete_person(person_id: int, conn=Depends(get_conn)) -> Response:
    people_svc.delete_person(conn, person_id)
    return Response(status_code=204)


# --- Привязки к проекту ---


@router.get("/projects/{project_id}/people")
def list_project_people(project_id: int, conn=Depends(get_conn)) -> dict:
    return people_svc.list_project_people(conn, project_id)


@router.post("/projects/{project_id}/people", status_code=201)
def attach_project_person(
    project_id: int, payload: dict = Body(...), conn=Depends(get_conn)
) -> dict:
    return people_svc.attach_person_to_project(conn, project_id, payload)


@router.delete("/projects/{project_id}/people/{person_id}", status_code=204)
def detach_project_person(
    project_id: int, person_id: int, conn=Depends(get_conn)
) -> Response:
    people_svc.detach_person_from_project(conn, project_id, person_id)
    return Response(status_code=204)
