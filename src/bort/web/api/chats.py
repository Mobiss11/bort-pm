"""Роутер чатов: справочник, участники, привязки к проекту."""

from fastapi import APIRouter, Body, Depends, Response

from ... import errors
from ...services import chats as chats_svc
from ..deps import get_conn

router = APIRouter(prefix="/api/v1", tags=["chats"])


@router.get("/chats")
def list_chats(q: str | None = None, kind: str | None = None, conn=Depends(get_conn)) -> dict:
    return chats_svc.list_chats(conn, q=q, kind=kind)


@router.post("/chats", status_code=201)
def create_chat(payload: dict = Body(...), conn=Depends(get_conn)) -> dict:
    return chats_svc.create_chat(conn, payload)


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, conn=Depends(get_conn)) -> dict:
    return chats_svc.get_chat(conn, chat_id)


@router.patch("/chats/{chat_id}")
def update_chat(chat_id: int, payload: dict = Body(default={}), conn=Depends(get_conn)) -> dict:
    return chats_svc.update_chat(conn, chat_id, payload)


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: int, conn=Depends(get_conn)) -> Response:
    chats_svc.delete_chat(conn, chat_id)
    return Response(status_code=204)


@router.post("/chats/{chat_id}/members", status_code=201)
def add_chat_member(chat_id: int, payload: dict = Body(...), conn=Depends(get_conn)) -> dict:
    return chats_svc.add_member(conn, chat_id, payload)


@router.delete("/chats/{chat_id}/members/{person_id}", status_code=204)
def remove_chat_member(
    chat_id: int, person_id: int, conn=Depends(get_conn)
) -> Response:
    chats_svc.remove_member(conn, chat_id, person_id)
    return Response(status_code=204)


# --- Привязки к проекту ---


@router.get("/projects/{project_id}/chats")
def list_project_chats(project_id: int, conn=Depends(get_conn)) -> dict:
    return chats_svc.list_project_chats(conn, project_id)


@router.post("/projects/{project_id}/chats", status_code=201)
def attach_project_chat(
    project_id: int, payload: dict = Body(...), conn=Depends(get_conn)
) -> dict:
    note = payload.get("note")
    chat_id = payload.get("chat_id")
    if chat_id is not None:
        return chats_svc.attach_chat_to_project(conn, project_id, int(chat_id), note=note)
    if payload.get("title"):
        chat_data = {k: v for k, v in payload.items() if k != "note"}
        chat = chats_svc.create_chat(conn, chat_data)
        return chats_svc.attach_chat_to_project(conn, project_id, chat["id"], note=note)
    raise errors.ValidationError(
        "Нужен chat_id либо title для создания нового чата",
        details={"payload_keys": sorted(payload.keys())},
    )


@router.delete("/projects/{project_id}/chats/{chat_id}", status_code=204)
def detach_project_chat(
    project_id: int, chat_id: int, conn=Depends(get_conn)
) -> Response:
    chats_svc.detach_chat_from_project(conn, project_id, chat_id)
    return Response(status_code=204)
