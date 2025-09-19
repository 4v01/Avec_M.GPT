from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

@router.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}

# 兼容你前端里 /api/health 的探活
@router.get("/api/health", tags=["Health"])
def api_health():
    return {"status": "ok"}
