from __future__ import annotations

from pathlib import Path
from datetime import datetime
from uuid import uuid4
from typing import Any, Dict, Optional, List

from fastapi import FastAPI, Body, Query
from fastapi.routing import APIRoute
from api.services.exporter import router as exporter_router
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# 现有子路由
from api.routes_jobs import router as jobs_router
from api.routes_logs import router as logs_router
from api.routes_db import router as db_router
from api.routes_journal import router as journal_router
from api.utils.boards import DEFAULT_BOARDS  # 可选默认板块

# 关键：直接用已有的 scrapyd 封装
from api.services import scrapyd_runner  # list_projects, list_spiders, run_all 皆在这里

app = FastAPI(title="PR Platform API", version="1.0.0")

# ---------- 路由挂载 ----------
app.include_router(jobs_router)
app.include_router(logs_router)
# exporter 的路由定义均为无前缀，这里统一挂在 /api 下
app.include_router(exporter_router, prefix="/api", tags=["export"])  # 同步新版 exporter
app.include_router(journal_router)
app.include_router(db_router, prefix="/api", tags=["db"])

# CORS 放宽
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def api_health():
    return {"status": "ok"}

# ---------- 新增：路由自检 ----------
@app.get("/api/_diag/routes")
def list_routes():
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            out.append({
                "path": r.path,
                "methods": list(r.methods),
                "name": r.name,
            })
    return out

# ---------- 爬虫适配层 ----------
@app.post("/api/crawl", tags=["crawl"])
def api_crawl(
    payload: Dict[str, Any] = Body(
        ...,
        description="支持两种：1) 常规 keywords ；2) boards={板块:[kw,...]}（逐词并发）"
    ),
    project: Optional[str] = Query(None, description="可选：覆盖 Scrapyd 项目名"),
) -> JSONResponse:
    job_tag = str(payload.get("job_tag") or f"board-{datetime.now():%Y%m%d%H%M%S}-{uuid4().hex[:6]}")
    payload["job_tag"] = job_tag

    if project:
        payload["project"] = project

    boards = payload.get("boards")

    if isinstance(boards, str) and boards.strip() == "__default__":
        boards = DEFAULT_BOARDS

    results: List[Dict[str, Any]] = []

    def _norm_list(v) -> List[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        s = str(v or "").replace("，", ",").replace("|", ",")
        return [t.strip() for t in s.split(",") if t.strip()]

    if isinstance(boards, dict) and boards:
        for board_name, kws in boards.items():
            for kw in _norm_list(kws):
                p = dict(payload)
                p["keywords"] = kw
                p["board_hint"] = board_name
                batch = scrapyd_runner.run_all(job_id=job_tag, payload=p)
                results.append({"board": board_name, "keyword": kw, "result": batch})
    else:
        batch = scrapyd_runner.run_all(job_id=job_tag, payload=payload)
        results.append({"keyword": str(payload.get("keywords") or ""), "result": batch})

    return JSONResponse({"job_tag": job_tag, "batches": results})

@app.get("/api/projects", tags=["crawl"])
def api_projects():
    return {"projects": scrapyd_runner.list_projects()}

@app.get("/api/spiders", tags=["crawl"])
def api_spiders(project: Optional[str] = Query(None, description="不传则用默认项目")):
    proj = project or scrapyd_runner.DEFAULT_PROJECT
    return {"project": proj, "spiders": scrapyd_runner.list_spiders(project=proj)}

# ---------- 静态前端 ----------
BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = BASE_DIR / "web"

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    assets_dir = WEB_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def index_root():
        index_file = WEB_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"detail": "index.html not found"}, status_code=404)

    app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:
    @app.get("/")
    def index_missing():
        return JSONResponse({"detail": "web/ 目录未找到。"}, status_code=404)

# 兼容：/api/export 表单（调试）
@app.get("/api/export", response_class=HTMLResponse)
def export_form():
    return """
    <html><body style="font-family: sans-serif; padding: 16px">
      <h3>/api/export.xlsx 调试表单</h3>
      <form action="/api/export.xlsx" method="get">
        <div>keywords: <input name="keywords" style="width: 320px"/></div>
        <div>start: <input name="start" placeholder="YYYY-MM-DD"/></div>
        <div>end: <input name="end" placeholder="YYYY-MM-DD"/></div>
        <div>spiders: <input name="spiders" placeholder="dayoo_search,nfnews_search" style="width: 320px"/></div>
        <div>job_tag: <input name="job_tag" value="manual"/></div>
        <button type="submit">下载 export.xlsx</button>
      </form>
    </body></html>
    """
