from __future__ import annotations
from typing import Any, Dict
from fastapi import APIRouter, Query, Body
from api.services.journal import list_journals, refresh_journal, delete_journal

router = APIRouter(prefix="/api/journal", tags=["journal"])

@router.get("/list")
def api_list() -> Any:
    return list_journals()

@router.post("/refresh")
def api_refresh(
    date: str = Query(..., description="YYYY-MM-DD"),
    boards: Dict[str, Any] | None = Body(None, description="板块关键词 JSON"),
) -> Any:
    return refresh_journal(date, boards or None)

@router.post("/delete")
def api_delete(date: str = Query(..., description="YYYY-MM-DD")) -> Any:
    return delete_journal(date)