"""Связь проектов Борта с читаемыми локальными проектами Beads."""

import sqlite3

from .. import errors
from . import projects as projects_svc


def all_links(conn) -> tuple[dict[int, str], dict[str, dict]]:
    rows = conn.execute(
        "SELECT l.project_id, l.beads_project, p.name AS bort_name "
        "FROM project_beads_links l JOIN projects p ON p.id = l.project_id"
    ).fetchall()
    return (
        {row["project_id"]: row["beads_project"] for row in rows},
        {row["beads_project"]: {"id": row["project_id"], "name": row["bort_name"]} for row in rows},
    )


def set_link(conn, project_id: int, beads_project: str, available: set[str]) -> None:
    projects_svc.get_project(conn, project_id)
    if beads_project not in available:
        raise errors.ValidationError("Выберите доступный проект задач")
    try:
        conn.execute(
            "INSERT INTO project_beads_links (project_id, beads_project) VALUES (?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET beads_project = excluded.beads_project, "
            "linked_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            (project_id, beads_project),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise errors.Conflict("Этот проект задач уже привязан к другому проекту Борта") from exc


def remove_link(conn, project_id: int) -> None:
    projects_svc.get_project(conn, project_id)
    conn.execute("DELETE FROM project_beads_links WHERE project_id = ?", (project_id,))
    conn.commit()
