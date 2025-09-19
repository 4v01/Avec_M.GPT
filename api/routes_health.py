from __future__ import annotations
from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

router = APIRouter(prefix="/api")

@router.get("/_diag/routes")
def list_routes():
    from api.main import app
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            out.append({
                "path": r.path,
                "methods": list(r.methods),
                "name": r.name,
            })
    return out
@router.get("/health")
def health():
    return {"status": "ok"}
