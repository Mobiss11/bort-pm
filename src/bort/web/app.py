"""FastAPI-приложение: роутеры, единый конверт ошибок, /api/v1/health.

Слушаем только 127.0.0.1 (config.BORT_HOST) — аутентификации в v1 нет (§8/§9).
Запуск: uv run python -m bort.web.app  или  uvicorn bort.web.app:app
"""

import sqlite3

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import config, db, errors
from . import deps, pages
from .api import chats, expenses, meta, payments, people, projects, summary, tasks


def _error_content(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def create_app() -> FastAPI:
    app = FastAPI(title="Борт", version="0.1.0", docs_url="/docs")

    app.include_router(summary.router)
    app.include_router(projects.router)
    app.include_router(tasks.router)
    app.include_router(expenses.router)
    app.include_router(payments.router)
    app.include_router(chats.router)
    app.include_router(people.router)
    app.include_router(meta.router)
    app.include_router(pages.router)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/api/v1/health", tags=["meta"])
    def health(conn=Depends(deps.get_conn)):
        return {
            "status": "ok",
            "db_path": config.db_path(),
            "schema_version": db.schema_version(conn),
            "wal": db.wal_enabled(conn),
        }

    @app.exception_handler(errors.BortError)
    async def bort_error_handler(request: Request, exc: errors.BortError) -> JSONResponse:
        if isinstance(exc, errors.NotFound):
            status = 404
        elif isinstance(exc, errors.ValidationError):
            status = 422
        elif isinstance(exc, errors.Conflict):
            status = 409
        else:
            status = 500
        return JSONResponse(
            status_code=status,
            content=_error_content(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error_handler(request: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
        # Страховка: сервисы проверяют конфликтные случаи заранее
        return JSONResponse(
            status_code=409,
            content=_error_content("conflict", "Нарушение ограничения целостности данных"),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details: dict = {}
        for err in exc.errors():
            key = ".".join(str(loc) for loc in err.get("loc", []) if loc != "body") or "__root__"
            details.setdefault(key, []).append(err.get("msg", "некорректное значение"))
        return JSONResponse(
            status_code=422,
            content=_error_content("validation", "Ошибка валидации запроса", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {
            404: "not_found",
            405: "method_not_allowed",
            422: "validation",
            409: "conflict",
        }.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_error_content("internal", "Внутренняя ошибка сервера"),
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.host(), port=config.port())


if __name__ == "__main__":
    main()
