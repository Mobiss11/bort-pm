"""Справочник Telegram-чатов и привязки к проектам.

Чат — независимая сущность: переживает отвязку от проекта и удаление проекта.
"""

import sqlite3

from .. import errors
from ..models import ChatCreate, ChatMemberAdd, ChatUpdate, validate_payload
from . import people, projects

_KINDS = {"private", "group"}


def _chat_row(conn, chat_id: int):
    return conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()


def create_chat(conn, data: dict) -> dict:
    payload = validate_payload(ChatCreate, data)
    try:
        cur = conn.execute(
            "INSERT INTO chats (kind, title, tg_chat_id, tg_link, notes) VALUES (?, ?, ?, ?, ?)",
            (payload.kind, payload.title, payload.tg_chat_id, payload.tg_link, payload.notes),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            "Чат с таким tg_chat_id уже существует",
            details={"tg_chat_id": payload.tg_chat_id},
        ) from e
    return get_chat(conn, cur.lastrowid)


def get_chat(conn, chat_id: int) -> dict:
    row = _chat_row(conn, chat_id)
    if row is None:
        raise errors.NotFound(f"Чат {chat_id} не найден", details={"chat_id": chat_id})
    chat = dict(row)
    chat["members"] = [
        dict(r)
        for r in conn.execute(
            """
            SELECT cm.person_id, cm.role, cm.created_at AS member_since,
                   p.full_name, p.tg_username
            FROM chat_members cm
            JOIN people p ON p.id = cm.person_id
            WHERE cm.chat_id = ?
            ORDER BY p.full_name COLLATE NOCASE, p.id
            """,
            (chat_id,),
        )
    ]
    chat["projects"] = [
        dict(r)
        for r in conn.execute(
            """
            SELECT p.id, p.name, p.status, pc.note, pc.created_at AS linked_at
            FROM project_chats pc
            JOIN projects p ON p.id = pc.project_id
            WHERE pc.chat_id = ?
            ORDER BY p.name COLLATE NOCASE, p.id
            """,
            (chat_id,),
        )
    ]
    return chat


def list_chats(conn, *, q: str | None = None, kind: str | None = None) -> dict:
    if kind is not None and kind not in _KINDS:
        raise errors.ValidationError(
            f"Неизвестный тип чата: {kind}", details={"allowed": sorted(_KINDS)}
        )
    clauses, params = [], []
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM chats {where} ORDER BY title COLLATE NOCASE, id", params
    ).fetchall()
    items = [dict(r) for r in rows]
    if q:
        needle = str(q).casefold()
        items = [
            c
            for c in items
            if needle in c["title"].casefold() or (c["tg_chat_id"] and needle in c["tg_chat_id"].casefold())
        ]
    return {"items": items, "total": len(items)}


def update_chat(conn, chat_id: int, data: dict) -> dict:
    if _chat_row(conn, chat_id) is None:
        raise errors.NotFound(f"Чат {chat_id} не найден", details={"chat_id": chat_id})
    fields = validate_payload(ChatUpdate, data).model_dump(exclude_unset=True)
    if not fields:
        return get_chat(conn, chat_id)
    cols = ", ".join(f"{name} = ?" for name in fields)
    try:
        conn.execute(f"UPDATE chats SET {cols} WHERE id = ?", [*fields.values(), chat_id])
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            "Чат с таким tg_chat_id уже существует",
            details={"tg_chat_id": fields.get("tg_chat_id")},
        ) from e
    return get_chat(conn, chat_id)


def delete_chat(conn, chat_id: int) -> None:
    if _chat_row(conn, chat_id) is None:
        raise errors.NotFound(f"Чат {chat_id} не найден", details={"chat_id": chat_id})
    conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()


def add_member(conn, chat_id: int, data: dict) -> dict:
    if _chat_row(conn, chat_id) is None:
        raise errors.NotFound(f"Чат {chat_id} не найден", details={"chat_id": chat_id})
    payload = validate_payload(ChatMemberAdd, data)
    if payload.person_id is not None:
        person = people.get_person(conn, payload.person_id)
    elif payload.full_name:
        person = people.create_person(
            conn, {"full_name": payload.full_name, "tg_username": payload.tg_username}
        )
    else:
        raise errors.ValidationError(
            "Нужен person_id либо full_name для создания участника",
            details={"person_id": payload.person_id, "full_name": payload.full_name},
        )
    try:
        conn.execute(
            "INSERT INTO chat_members (chat_id, person_id, role) VALUES (?, ?, ?)",
            (chat_id, person["id"], payload.role),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            f"Человек {person['full_name']} уже состоит в чате",
            details={"chat_id": chat_id, "person_id": person["id"]},
        ) from e
    return {
        "chat_id": chat_id,
        "person_id": person["id"],
        "role": payload.role,
        "full_name": person["full_name"],
        "tg_username": person["tg_username"],
    }


def remove_member(conn, chat_id: int, person_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM chat_members WHERE chat_id = ? AND person_id = ?",
        (chat_id, person_id),
    ).fetchone()
    if row is None:
        raise errors.NotFound(
            "Участник не найден в чате",
            details={"chat_id": chat_id, "person_id": person_id},
        )
    conn.execute(
        "DELETE FROM chat_members WHERE chat_id = ? AND person_id = ?",
        (chat_id, person_id),
    )
    conn.commit()


# --- Привязки к проекту ---


def list_project_chats(conn, project_id: int) -> dict:
    projects.get_project(conn, project_id)
    rows = conn.execute(
        """
        SELECT c.id, c.kind, c.title, c.tg_chat_id, c.tg_link, c.notes,
               pc.note AS link_note, pc.created_at AS linked_at
        FROM project_chats pc
        JOIN chats c ON c.id = pc.chat_id
        WHERE pc.project_id = ?
        ORDER BY c.title COLLATE NOCASE, c.id
        """,
        (project_id,),
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


def attach_chat_to_project(conn, project_id: int, chat_id: int, note: str | None = None) -> dict:
    projects.get_project(conn, project_id)
    if _chat_row(conn, chat_id) is None:
        raise errors.NotFound(f"Чат {chat_id} не найден", details={"chat_id": chat_id})
    try:
        conn.execute(
            "INSERT INTO project_chats (project_id, chat_id, note) VALUES (?, ?, ?)",
            (project_id, chat_id, note),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            "Чат уже привязан к проекту",
            details={"project_id": project_id, "chat_id": chat_id},
        ) from e
    return get_chat(conn, chat_id)


def detach_chat_from_project(conn, project_id: int, chat_id: int) -> None:
    projects.get_project(conn, project_id)
    row = conn.execute(
        "SELECT 1 FROM project_chats WHERE project_id = ? AND chat_id = ?",
        (project_id, chat_id),
    ).fetchone()
    if row is None:
        raise errors.NotFound(
            "Чат не привязан к проекту",
            details={"project_id": project_id, "chat_id": chat_id},
        )
    conn.execute(
        "DELETE FROM project_chats WHERE project_id = ? AND chat_id = ?",
        (project_id, chat_id),
    )
    conn.commit()
    # Чат остаётся в справочнике — удаляется только связь
