"""REST API журнала AI-запросов и снимков лимитов."""

from fastapi import APIRouter, Body, Depends, Query

from ...services import ai_usage as ai_svc
from ..deps import get_conn

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


@router.get("/overview")
def overview(conn=Depends(get_conn)) -> dict:
    return ai_svc.dashboard(conn)


@router.get("/usage")
def list_usage(
    provider: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    conn=Depends(get_conn),
) -> dict:
    return ai_svc.list_usage(conn, provider=provider, limit=limit)


@router.post("/usage", status_code=201)
def record_usage(payload: dict = Body(...), conn=Depends(get_conn)) -> dict:
    return ai_svc.record_usage(conn, payload)


@router.post("/providers/{provider}/refresh")
def refresh_provider(provider: str, conn=Depends(get_conn)) -> dict:
    refresher = ai_svc.REFRESHERS.get(provider)
    if refresher is None:
        from ... import errors

        raise errors.NotFound(f"AI-провайдер {provider!r} не поддерживается", details={"provider": provider})
    if provider == "codex":
        return ai_svc.refresh_codex(conn, request_refresh=True)
    return refresher(conn)
