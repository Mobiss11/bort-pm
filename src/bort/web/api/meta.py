"""Справочники статусов и приоритетов с русскими подписями (единый источник для UI и MCP)."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["meta"])

# Единый источник русских подписей: REST /meta/enums, Jinja-фильтры и MCP
ENUMS = {
    "project_statuses": [
        ("idea", "Идея"),
        ("active", "В работе"),
        ("paused", "На паузе"),
        ("closed", "Закрыт"),
    ],
    "task_statuses": [
        ("todo", "К выполнению"),
        ("in_progress", "В работе"),
        ("review", "На проверке"),
        ("done", "Готово"),
        ("cancelled", "Отменено"),
    ],
    "priorities": [
        (1, "Критичный"),
        (2, "Высокий"),
        (3, "Обычный"),
        (4, "Низкий"),
    ],
    "chat_kinds": [
        ("private", "Личный"),
        ("group", "Группа"),
    ],
}


@router.get("/meta/enums")
def get_enums() -> dict:
    return {
        name: [{"code": code, "title_ru": title} for code, title in items]
        for name, items in ENUMS.items()
    }
