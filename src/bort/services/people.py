"""Справочник людей и привязки к проектам."""

import sqlite3

from .. import errors
from ..models import ProjectPersonAdd, PersonCreate, PersonUpdate, validate_payload
from . import projects


def _person_row(conn, person_id: int):
    return conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()


def create_person(conn, data: dict) -> dict:
    payload = validate_payload(PersonCreate, data)
    try:
        cur = conn.execute(
            "INSERT INTO people (full_name, tg_username, notes) VALUES (?, ?, ?)",
            (payload.full_name, payload.tg_username, payload.notes),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            "Человек с таким tg_username уже существует",
            details={"tg_username": payload.tg_username},
        ) from e
    return get_person(conn, cur.lastrowid)


def get_person(conn, person_id: int) -> dict:
    row = _person_row(conn, person_id)
    if row is None:
        raise errors.NotFound(f"Человек {person_id} не найден", details={"person_id": person_id})
    person = dict(row)
    person["chats"] = [
        dict(r)
        for r in conn.execute(
            """
            SELECT c.id, c.title, c.kind, cm.role, cm.created_at AS member_since
            FROM chat_members cm
            JOIN chats c ON c.id = cm.chat_id
            WHERE cm.person_id = ?
            ORDER BY c.title COLLATE NOCASE, c.id
            """,
            (person_id,),
        )
    ]
    person["projects"] = [
        dict(r)
        for r in conn.execute(
            """
            SELECT p.id, p.name, p.status, pp.role, pp.created_at AS linked_at
            FROM project_people pp
            JOIN projects p ON p.id = pp.project_id
            WHERE pp.person_id = ?
            ORDER BY p.name COLLATE NOCASE, p.id
            """,
            (person_id,),
        )
    ]
    return person


def list_people(conn, *, q: str | None = None) -> dict:
    rows = conn.execute(
        "SELECT * FROM people ORDER BY full_name COLLATE NOCASE, id"
    ).fetchall()
    items = [dict(r) for r in rows]
    if q:
        needle = str(q).casefold()
        items = [
            p
            for p in items
            if needle in p["full_name"].casefold()
            or (p["tg_username"] and needle in p["tg_username"].casefold())
        ]
    return {"items": items, "total": len(items)}


def update_person(conn, person_id: int, data: dict) -> dict:
    if _person_row(conn, person_id) is None:
        raise errors.NotFound(f"Человек {person_id} не найден", details={"person_id": person_id})
    fields = validate_payload(PersonUpdate, data).model_dump(exclude_unset=True)
    if not fields:
        return get_person(conn, person_id)
    cols = ", ".join(f"{name} = ?" for name in fields)
    try:
        conn.execute(f"UPDATE people SET {cols} WHERE id = ?", [*fields.values(), person_id])
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            "Человек с таким tg_username уже существует",
            details={"tg_username": fields.get("tg_username")},
        ) from e
    return get_person(conn, person_id)


def delete_person(conn, person_id: int) -> None:
    if _person_row(conn, person_id) is None:
        raise errors.NotFound(f"Человек {person_id} не найден", details={"person_id": person_id})
    conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
    conn.commit()


# --- Привязки к проекту ---


def list_project_people(conn, project_id: int) -> dict:
    projects.get_project(conn, project_id)
    rows = conn.execute(
        """
        SELECT pe.id AS person_id, pe.full_name, pe.tg_username,
               pp.role, pp.created_at AS linked_at
        FROM project_people pp
        JOIN people pe ON pe.id = pp.person_id
        WHERE pp.project_id = ?
        ORDER BY pe.full_name COLLATE NOCASE, pe.id
        """,
        (project_id,),
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


def attach_person_to_project(conn, project_id: int, data: dict) -> dict:
    projects.get_project(conn, project_id)
    payload = validate_payload(ProjectPersonAdd, data)
    if payload.person_id is not None:
        person = get_person(conn, payload.person_id)
    elif payload.full_name:
        person = create_person(
            conn, {"full_name": payload.full_name, "tg_username": payload.tg_username}
        )
    else:
        raise errors.ValidationError(
            "Нужен person_id либо full_name",
            details={"person_id": payload.person_id, "full_name": payload.full_name},
        )
    try:
        conn.execute(
            "INSERT INTO project_people (project_id, person_id, role) VALUES (?, ?, ?)",
            (project_id, person["id"], payload.role),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise errors.Conflict(
            f"Человек {person['full_name']} уже привязан к проекту",
            details={"project_id": project_id, "person_id": person["id"]},
        ) from e
    return get_person(conn, person["id"])


def detach_person_from_project(conn, project_id: int, person_id: int) -> None:
    projects.get_project(conn, project_id)
    row = conn.execute(
        "SELECT 1 FROM project_people WHERE project_id = ? AND person_id = ?",
        (project_id, person_id),
    ).fetchone()
    if row is None:
        raise errors.NotFound(
            "Человек не привязан к проекту",
            details={"project_id": project_id, "person_id": person_id},
        )
    conn.execute(
        "DELETE FROM project_people WHERE project_id = ? AND person_id = ?",
        (project_id, person_id),
    )
    conn.commit()
