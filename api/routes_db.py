# api/routes_db.py
from __future__ import annotations
import os, shutil, sqlite3, time
from typing import Any, Dict
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

# 复用 exporter 的 DB 解析逻辑
from api.services.exporter import _connect, _detect_table, _list_cols, _resolve_db  # type: ignore

router = APIRouter()

@router.get("/_diag/db")
def db_info() -> Dict[str, Any]:
    db_path, _ = _resolve_db()
    conn = _connect()
    table = _detect_table(conn)
    cols = _list_cols(conn, table) if table else []
    cur = conn.cursor()
    total = 0
    if table:
        cur.execute(f"SELECT COUNT(1) FROM {table}")
        total = cur.fetchone()[0]
    # seen 去重库路径（与 settings 的默认一致）
    pr_root = os.environ.get("PR_RUNTIME_DIR") or os.getcwd()
    seen_path = os.path.join(pr_root, "var", "seen_urls.sqlite3")
    conn.close()
    return {
        "db_path": db_path, "table": table, "cols": cols, "total_rows": total,
        "seen_path": seen_path, "seen_exists": os.path.exists(seen_path),
    }

@router.post("/_diag/db/reset")
def db_reset() -> Dict[str, Any]:
    db_path, _ = _resolve_db()
    conn = _connect()
    table = _detect_table(conn)
    cur = conn.cursor()
    if table:
        cur.execute(f"DELETE FROM {table}")
        conn.commit()
    conn.close()
    # 清空去重库
    pr_root = os.environ.get("PR_RUNTIME_DIR") or os.getcwd()
    seen_path = os.path.join(pr_root, "var", "seen_urls.sqlite3")
    if os.path.exists(seen_path):
        try: os.remove(seen_path)
        except Exception as e:
            raise HTTPException(500, f"remove seen db failed: {e}")
    return {"status": "ok", "db_cleared": True, "seen_cleared": True, "db_path": db_path}

@router.post("/_diag/db/vacuum")
def db_vacuum() -> Dict[str, Any]:
    conn = _connect()
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok"}

@router.post("/_diag/db/snapshot")
def db_snapshot() -> Dict[str, Any]:
    db_path, _ = _resolve_db()
    if not os.path.exists(db_path):
        raise HTTPException(404, "db not found")
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst_dir = os.path.join(os.path.dirname(db_path), "snapshots")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"items_{ts}.sqlite3")
    shutil.copy2(db_path, dst)
    return {"status": "ok", "snapshot": dst}
