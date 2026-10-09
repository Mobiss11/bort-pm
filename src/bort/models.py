"""Pydantic-схемы запросов/ответов REST API.

Даты — строки YYYY-MM-DD (валидация календарная), деньги — INTEGER копейки.
"""

import re
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timezone as _timezone
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from . import errors

ProjectStatus = Literal["idea", "discussion", "active", "review", "payment", "paused", "closed"]
TaskStatus = Literal["todo", "in_progress", "review", "done", "cancelled"]
ChatKind = Literal["private", "group"]
PaymentKind = Literal["prepayment", "partial", "final"]
WorkFormat = Literal["full_time", "part_time", "contract", "other"]
WorkStatus = Literal["active", "paused", "finished"]
AIUsageStatus = Literal["success", "error"]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^[a-z0-9_]+$")


def _check_date(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str) or not _DATE_RE.match(v):
        raise ValueError("Дата должна быть строкой в формате YYYY-MM-DD")
    try:
        _date.fromisoformat(v)
    except ValueError as e:
        raise ValueError(f"Некорректная дата: {v!r}") from e
    return v


def _clean_required_text(v: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError("Обязательное текстовое поле не может быть пустым")
    return v.strip()


def _clean_optional_text(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("Ожидается строка")
    v = v.strip()
    return v or None


def _check_currency(v: str) -> str:
    if not isinstance(v, str) or len(v) != 3 or not v.isalpha():
        raise ValueError("Валюта — трёхбуквенный код (например, RUB)")
    return v.upper()


def _check_tg_username(v: Optional[str]) -> Optional[str]:
    v = _clean_optional_text(v)
    if v and v.startswith("@"):
        v = v[1:]
    return v or None


def _clean_code(v: str) -> str:
    v = _clean_required_text(v).lower()
    if not re.match(r"^[a-z0-9_-]+$", v):
        raise ValueError("Код: строчные латинские буквы, цифры, _ или -")
    return v


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Проекты ---


class ProjectCreate(_Base):
    name: str
    status: ProjectStatus = "idea"
    priority: int = Field(default=2, ge=1, le=4)
    deal_amount_minor: int = Field(default=0, ge=0)
    currency: str = "RUB"
    deadline: Optional[str] = None
    started_on: Optional[str] = None
    finished_on: Optional[str] = None
    notes: Optional[str] = None

    _n = field_validator("name")(_clean_required_text)
    _c = field_validator("currency")(_check_currency)
    _d = field_validator("deadline", "started_on", "finished_on")(_check_date)


class ProjectUpdate(_Base):
    name: Optional[str] = None
    status: Optional[ProjectStatus] = None
    priority: Optional[int] = Field(default=None, ge=1, le=4)
    deal_amount_minor: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = None
    deadline: Optional[str] = None
    started_on: Optional[str] = None
    finished_on: Optional[str] = None
    notes: Optional[str] = None

    _n = field_validator("name")(_clean_required_text)
    _c = field_validator("currency")(_check_currency)
    _d = field_validator("deadline", "started_on", "finished_on")(_check_date)


# --- Задачи ---


class TaskCreate(_Base):
    title: str
    status: TaskStatus = "todo"
    priority: int = Field(default=3, ge=1, le=4)
    deadline: Optional[str] = None
    notes: Optional[str] = None
    position: int = 0

    _t = field_validator("title")(_clean_required_text)
    _d = field_validator("deadline")(_check_date)


class TaskUpdate(_Base):
    title: Optional[str] = None
    status: Optional[TaskStatus] = None
    priority: Optional[int] = Field(default=None, ge=1, le=4)
    deadline: Optional[str] = None
    notes: Optional[str] = None
    position: Optional[int] = None

    _t = field_validator("title")(_clean_required_text)
    _d = field_validator("deadline")(_check_date)


class TaskClose(_Base):
    status: Literal["done", "cancelled"] = "done"


# --- Затраты ---


class ExpenseCreate(_Base):
    amount_minor: int = Field(gt=0)
    currency: str = "RUB"
    spent_on: str
    category_code: str
    comment: Optional[str] = None

    _c = field_validator("currency")(_check_currency)
    _s = field_validator("spent_on")(_check_date)
    _cat = field_validator("category_code")(_clean_required_text)


class ExpenseUpdate(_Base):
    amount_minor: Optional[int] = Field(default=None, gt=0)
    currency: Optional[str] = None
    spent_on: Optional[str] = None
    category_code: Optional[str] = None
    comment: Optional[str] = None

    _c = field_validator("currency")(_check_currency)
    _s = field_validator("spent_on")(_check_date)
    _cat = field_validator("category_code")(_clean_required_text)


class CategoryCreate(_Base):
    code: str
    title_ru: str
    sort_order: int = 100

    _co = field_validator("code")(_clean_required_text)
    _t = field_validator("title_ru")(_clean_required_text)

    @field_validator("code")
    @classmethod
    def _code_format(cls, v: str) -> str:
        if not _CODE_RE.match(v):
            raise ValueError("Код категории: строчные латинские буквы, цифры, _")
        return v


# --- Чаты и люди ---


class ChatCreate(_Base):
    title: str
    kind: ChatKind = "group"
    tg_chat_id: Optional[str] = None
    tg_link: Optional[str] = None
    notes: Optional[str] = None

    _t = field_validator("title")(_clean_required_text)
    _o = field_validator("tg_chat_id", "tg_link", "notes")(_clean_optional_text)


class ChatUpdate(_Base):
    title: Optional[str] = None
    kind: Optional[ChatKind] = None
    tg_chat_id: Optional[str] = None
    tg_link: Optional[str] = None
    notes: Optional[str] = None

    _t = field_validator("title")(_clean_required_text)
    _o = field_validator("tg_chat_id", "tg_link", "notes")(_clean_optional_text)


class PersonCreate(_Base):
    full_name: str
    tg_username: Optional[str] = None
    notes: Optional[str] = None

    _f = field_validator("full_name")(_clean_required_text)
    _u = field_validator("tg_username")(_check_tg_username)
    _n = field_validator("notes")(_clean_optional_text)


class PersonUpdate(_Base):
    full_name: Optional[str] = None
    tg_username: Optional[str] = None
    notes: Optional[str] = None

    _f = field_validator("full_name")(_clean_required_text)
    _u = field_validator("tg_username")(_check_tg_username)
    _n = field_validator("notes")(_clean_optional_text)


class ChatMemberAdd(_Base):
    person_id: Optional[int] = None
    full_name: Optional[str] = None
    tg_username: Optional[str] = None
    role: Optional[str] = None

    _f = field_validator("full_name")(_clean_required_text)
    _u = field_validator("tg_username")(_check_tg_username)
    _r = field_validator("role")(_clean_optional_text)


class ChatAttach(_Base):
    """Привязка чата к проекту: существующий chat_id ИЛИ поля нового чата."""

    chat_id: Optional[int] = None
    title: Optional[str] = None
    kind: ChatKind = "group"
    tg_chat_id: Optional[str] = None
    tg_link: Optional[str] = None
    notes: Optional[str] = None
    note: Optional[str] = None

    _t = field_validator("title")(_clean_required_text)
    _o = field_validator("tg_chat_id", "tg_link", "notes", "note")(_clean_optional_text)


class ProjectPersonAdd(_Base):
    person_id: Optional[int] = None
    full_name: Optional[str] = None
    tg_username: Optional[str] = None
    role: Optional[str] = None

    _f = field_validator("full_name")(_clean_required_text)
    _u = field_validator("tg_username")(_check_tg_username)
    _r = field_validator("role")(_clean_optional_text)


# --- Платежи (стадии оплаты проекта) ---


class PaymentCreate(_Base):
    amount_minor: int = Field(gt=0)
    paid_on: str
    kind: PaymentKind = "partial"
    comment: Optional[str] = None

    _s = field_validator("paid_on")(_check_date)
    _c = field_validator("comment")(_clean_optional_text)


# --- Работа (справочник постоянных проектов) ---


class WorkProjectCreate(_Base):
    name: str
    client: Optional[str] = None
    role: Optional[str] = None
    format: WorkFormat = "other"
    monthly_amount_minor: int = Field(default=0, ge=0)
    currency: str = "RUB"
    status: WorkStatus = "active"
    started_on: Optional[str] = None
    notes: Optional[str] = None
    url: Optional[str] = None

    _n = field_validator("name")(_clean_required_text)
    _o = field_validator("client", "role", "notes", "url")(_clean_optional_text)
    _c = field_validator("currency")(_check_currency)
    _d = field_validator("started_on")(_check_date)


class WorkProjectUpdate(_Base):
    name: Optional[str] = None
    client: Optional[str] = None
    role: Optional[str] = None
    format: Optional[WorkFormat] = None
    monthly_amount_minor: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = None
    status: Optional[WorkStatus] = None
    started_on: Optional[str] = None
    notes: Optional[str] = None
    url: Optional[str] = None

    _n = field_validator("name")(_clean_required_text)
    _o = field_validator("client", "role", "notes", "url")(_clean_optional_text)
    _c = field_validator("currency")(_check_currency)
    _d = field_validator("started_on")(_check_date)


# --- Использование AI ---


class AIUsageCreate(_Base):
    provider: str
    agent: str = "unknown"
    model: str
    project_id: Optional[int] = Field(default=None, gt=0)
    external_request_id: Optional[str] = Field(default=None, max_length=255)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)
    status: AIUsageStatus = "success"
    error_code: Optional[str] = Field(default=None, max_length=255)
    occurred_at: Optional[str] = None

    _p = field_validator("provider")(_clean_code)
    _a = field_validator("agent")(_clean_required_text)
    _m = field_validator("model")(_clean_required_text)
    _r = field_validator("external_request_id", "error_code")(_clean_optional_text)

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at(cls, value: Optional[str]) -> Optional[str]:
        value = _clean_optional_text(value)
        if value is None:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = _datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("Время должно быть в формате ISO 8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("Время должно содержать часовой пояс")
        return parsed.astimezone(_timezone.utc).isoformat().replace("+00:00", "Z")


# --- Ответы (сущности) ---


class Project(_Base):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    name: str
    status: ProjectStatus
    priority: int
    deal_amount_minor: int
    currency: str
    deadline: Optional[str] = None
    started_on: Optional[str] = None
    finished_on: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    updated_at: str


class Task(_Base):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    project_id: int
    title: str
    status: TaskStatus
    priority: int
    deadline: Optional[str] = None
    notes: Optional[str] = None
    position: int
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None


class Expense(_Base):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    project_id: int
    amount_minor: int
    currency: str
    spent_on: str
    category_code: str
    comment: Optional[str] = None
    created_at: str
    updated_at: str


class Chat(_Base):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    kind: ChatKind
    title: str
    tg_chat_id: Optional[str] = None
    tg_link: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    updated_at: str


class Person(_Base):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    full_name: str
    tg_username: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    updated_at: str


def validate_payload(model_cls, data: dict):
    """Валидация словаря схемой; pydantic-ошибка → доменная ValidationError с details."""
    if not isinstance(data, dict):
        raise errors.ValidationError("Ожидается JSON-объект", details={"got": type(data).__name__})
    try:
        return model_cls(**data)
    except PydanticValidationError as e:
        details: dict = {}
        for err in e.errors():
            key = ".".join(str(loc) for loc in err["loc"]) or "__root__"
            details.setdefault(key, []).append(err["msg"])
        raise errors.ValidationError("Ошибка валидации данных", details=details) from e


class Category(_Base):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    code: str
    title_ru: str
    sort_order: int
    is_active: bool


class Payment(_Base):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    project_id: int
    amount_minor: int
    paid_on: str
    kind: PaymentKind
    comment: Optional[str] = None
    created_at: str
