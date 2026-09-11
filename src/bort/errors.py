"""Доменные ошибки. REST-слой конвертирует их в единый конверт с HTTP-кодами."""


class BortError(Exception):
    code = "error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(BortError):
    code = "not_found"


class ValidationError(BortError):
    code = "validation"


class Conflict(BortError):
    code = "conflict"
