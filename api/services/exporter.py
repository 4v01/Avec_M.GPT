# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import os
import re
import json
import time
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from urllib.parse import urlparse

from fastapi import APIRouter, Query, Body, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from openpyxl import Workbook

# 统一调度入口（已支持 date_start/date_end 优先）
from api.services import scrapyd_runner

# 可选：默认板块词库与解析（环境里没有也能跑）
try:
    from api.utils.boards import DEFAULT_BOARDS, parse_boards_any
except Exception:
    DEFAULT_BOARDS = {}
    def parse_boards_any(a, b):
        return {}

router = APIRouter()

# ---------------------- DB 解析与连接 ----------------------

PR_RUNTIME_DIR = os.getenv("PR_RUNTIME_DIR") or os.getcwd()

CANDIDATE_DBS = [
    os.getenv("NEWS_DB_PATH", "").strip(),
    os.path.join(PR_RUNTIME_DIR, "var", "data", "items.sqlite3"),
    os.path.join("scripts", "windows", "var", "data", "items.sqlite3"),
    os.path.join("var", "data", "items.sqlite3"),
    os.path.join("data", "items.sqlite3"),
    os.path.join("data", "news.db"),
    "items.sqlite3",
]

_DB_CACHE: Optional[str] = None
_TABLE_CACHE: Optional[str] = None


def _db_has_rows(path: str) -> tuple[int, Optional[str]]:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return (0, None)
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        for t in ("news", "items"):
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,))
            if cur.fetchone():
                cur.execute(f"SELECT COUNT(1) AS c FROM {t}")
                n = cur.fetchone()["c"]
                conn.close()
                return (int(n or 0), t)
        conn.close()
        return (0, None)
    except Exception:
        return (0, None)


def _split_keywords(s: str) -> List[str]:
    if not s:
        return []
    s = s.replace("，", ",").replace("|", ",").replace("、", ",")
    parts = re.split(r"[,\s]+", s.strip())
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _resolve_db() -> tuple[str, Optional[str]]:
    global _DB_CACHE, _TABLE_CACHE
    env = os.getenv("NEWS_DB_PATH", "").strip()
    if env:
        n, t = _db_has_rows(env)
        _DB_CACHE, _TABLE_CACHE = env, (t or _TABLE_CACHE)
        if n > 0:
            return _DB_CACHE, _TABLE_CACHE

    scored = []
    for p in CANDIDATE_DBS:
        if not p:
            continue
        if os.path.exists(p):
            try:
                n, t = _db_has_rows(p)
                scored.append((n, os.path.getmtime(p), p, t))
            except Exception:
                scored.append((0, 0.0, p, None))
    if scored:
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        n, _, p, t = scored[0]
        _DB_CACHE, _TABLE_CACHE = p, (t or _TABLE_CACHE)
        return _DB_CACHE, _TABLE_CACHE

    fallback = os.path.join("data", "news.db")
    os.makedirs(os.path.dirname(fallback) or ".", exist_ok=True)
    _DB_CACHE, _TABLE_CACHE = fallback, _TABLE_CACHE
    return _DB_CACHE, _TABLE_CACHE

# 向后兼容：journal.py 会 import 这个符号
DB_PATH = os.path.join("data", "news.db")


def _connect() -> sqlite3.Connection:
    global DB_PATH
    db, _ = _resolve_db()
    DB_PATH = db
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _detect_table(conn: sqlite3.Connection) -> Optional[str]:
    global _TABLE_CACHE
    if _TABLE_CACHE:
        return _TABLE_CACHE
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r[0] for r in cur.fetchall()}
    except Exception:
        names = set()
    if "news" in names:
        _TABLE_CACHE = "news"
    elif "items" in names:
        _TABLE_CACHE = "items"
    else:
        _TABLE_CACHE = None
    return _TABLE_CACHE


def _list_cols(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA table_info({table})")
        rows = cur.fetchall()
        return [r[1] if not isinstance(r, sqlite3.Row) else r["name"] for r in rows]
    except Exception:
        return []


def _preferred_order(cols: List[str]) -> List[str]:
    preferred = [
        "title","url","source","site","spider","date","published_at",
        "keywords","summary","author","job_tag","created_at","mode","page_no","pos",
    ]
    return [c for c in preferred if c in cols] + [c for c in cols if c not in preferred]

# ---------------------- 日期表达式与排序 ----------------------

def _date_where_frag(cols: List[str], start: str, end: str) -> Tuple[str, List[Any]]:
    """
    优先使用 published_at/date 的日期部分；两者都没有时才回退 created_at 的日期部分。
    """
    parts, params = [], []
    expr_pub = []
    if "published_at" in cols:
        expr_pub.append("CASE WHEN published_at GLOB '____-__-__*' THEN substr(published_at,1,10) END")
    if "date" in cols:
        expr_pub.append("CASE WHEN date GLOB '____-__-__*' THEN substr(date,1,10) END")
    expr_ca = "substr(created_at,1,10)" if "created_at" in cols else None

    if start:
        if expr_pub:
            parts.append("(" + " OR ".join([f"{e} >= ?" for e in expr_pub]) + (f" OR ({expr_ca} >= ? AND " + " AND ".join([f"{e} IS NULL" for e in expr_pub]) + ")" if expr_ca else "") + ")")
            params.extend([start] * len(expr_pub))
            if expr_ca:
                params.append(start)
        elif expr_ca:
            parts.append(f"{expr_ca} >= ?"); params.append(start)

    if end:
        if expr_pub:
            parts.append("(" + " OR ".join([f"{e} <= ?" for e in expr_pub]) + (f" OR ({expr_ca} <= ? AND " + " AND ".join([f"{e} IS NULL" for e in expr_pub]) + ")" if expr_ca else "") + ")")
            params.extend([end] * len(expr_pub))
            if expr_ca:
                params.append(end)
        elif expr_ca:
            parts.append(f"{expr_ca} <= ?"); params.append(end)

    if not parts:
        return "", []
    return "(" + " AND ".join(parts) + ")", params


def _order_sql(cols: List[str]) -> str:
    parts = []
    if "published_at" in cols:
        parts.append("CASE WHEN published_at GLOB '____-__-__*' THEN substr(published_at,1,10) END")
    if "date" in cols:
        parts.append("CASE WHEN date GLOB '____-__-__*' THEN substr(date,1,10) END")
    if "created_at" in cols:
        parts.append("substr(created_at,1,10)")
    return f" ORDER BY COALESCE({','.join(parts)}) DESC" if parts else ""

# ---------------------- 软过滤（搜索结果页去噪） ----------------------

def _looks_like_search_result(url: str) -> bool:
    try:
        u = urlparse(url or "")
        host = (u.netloc or "").lower()
        path = (u.path or "").lower()
        return ("searx" in host) and ("/search" in path or "/query" in path)
    except Exception:
        return False

# ---------------------- 查询与导出 ----------------------

def _build_where(
    cols: List[str],
    *,
    keywords: str,
    start: str,
    end: str,
    spiders: Optional[List[str]],
    job_tag: str,
    strict: int = 0,
    kw_logic: str = "OR",
    created_after: Optional[str] = None,
) -> Tuple[str, List[Any], str]:
    where, params = [], []
    kw_list = _split_keywords(keywords)

    # 关键词过滤
    if kw_list:
        if "keywords" in cols and strict:
            # 库里常为多关键词聚合字符串，严格模式用 LIKE 精确命中词元
            if kw_logic.upper() == "AND":
                where.extend(["keywords LIKE ?"] * len(kw_list))
                params.extend([f"%{kw}%" for kw in kw_list])
            else:
                where.append("(" + " OR ".join(["keywords LIKE ?"] * len(kw_list)) + ")")
                params.extend([f"%{kw}%" for kw in kw_list])
        else:
            likes_cols = [c for c in ("title", "summary", "source", "site", "keywords") if c in cols]
            if likes_cols:
                group_sqls = []
                for kw in kw_list:
                    like_sqls = [f"{c} LIKE ?" for c in likes_cols]
                    group_sqls.append("(" + " OR ".join(like_sqls) + ")")
                    params.extend([f"%{kw}%"] * len(like_sqls))
                joiner = " AND " if kw_logic.upper() == "AND" else " OR "
                where.append("(" + joiner.join(group_sqls) + ")")

    # job_tag 精确限制（若有列）
    if job_tag and "job_tag" in cols:
        where.append("job_tag LIKE ?")
        params.append(f"{job_tag}%")

    # created_after（只导本次）
    if created_after and "created_at" in cols:
        where.append("created_at >= ?")
        params.append(created_after)

    # 日期范围：published_at/date 优先，缺失再用 created_at
    frag, extra = _date_where_frag(cols, start, end)
    if frag:
        where.append(frag); params.extend(extra)

    # spiders 限制：优先 spider 列
    gk = "spider" if "spider" in cols else ("site" if "site" in cols else None)
    if spiders and gk:
        qs = ",".join(["?"] * len(spiders))
        where.append(f"{gk} IN ({qs})")
        params.extend(spiders)

    return (" WHERE " + " AND ".join(where)) if where else "", params, (gk or "site")


def _fetch_rows(
    *,
    keywords: str = "",
    start: str = "",
    end: str = "",
    spiders: Optional[List[str]] = None,
    job_tag: str = "",
    strict: int = 0,
    kw_logic: str = "OR",
    created_after: Optional[str] = None,
) -> Dict[str, Any]:
    if not start and not end:
        start = (datetime.now() - timedelta(hours=72)).date().isoformat()

    conn = _connect()
    table = _detect_table(conn)
    if not table:
        conn.close()
        return {"rows": [], "cols": ["title","url","source","site","spider","date","summary","keywords","created_at"], "group_key": "spider"}
    cols = _list_cols(conn, table)

    where_sql, params, gk = _build_where(
        cols,
        keywords=keywords, start=start, end=end, spiders=spiders,
        job_tag=job_tag, strict=strict, kw_logic=kw_logic,
        created_after=created_after,
    )
    order_sql = _order_sql(cols)

    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table}{where_sql}{order_sql}", params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    rows = [r for r in rows if not _looks_like_search_result(r.get("url", ""))]
    return {"rows": rows, "cols": cols, "group_key": gk}


def _write_xlsx(grouped: Dict[str, List[Dict[str, Any]]], cols: List[str], extra_cols: Optional[List[str]] = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    def _safe_sheet_name(name: str) -> str:
        # Excel sheet 名不能含下列字符且最长 31
        bad = ['\\', '/', '*', '[', ']', ':', '?']
        for ch in bad:
            name = name.replace(ch, ' ')
        name = name.strip()
        return name[:31] if len(name) > 31 else (name or "Sheet")

    wb = Workbook()
    # 注意：不要先删默认表。等确认至少会创建 1 张后再处理。
    default_ws = wb.active
    default_ws.title = "占位"  # 只是暂时占位，成功创建其它表后会移除

    order = _preferred_order(cols)
    extra_cols = extra_cols or []
    header = order + [c for c in extra_cols if c not in order]

    created_any = False

    for gname, rows in (grouped or {}).items():
        ws = wb.create_sheet(title=_safe_sheet_name(str(gname) or "Sheet"))
        # 头
        for j, h in enumerate(header, start=1):
            ws.cell(row=1, column=j, value=h)
        # 行
        for i, r in enumerate(rows, start=2):
            for j, h in enumerate(header, start=1):
                ws.cell(row=i, column=j, value=r.get(h, ""))
        created_any = True

    # 若没创建出任何表（例如分组为空或全被过滤），建立一个“总表(空)”
    if not created_any:
        ws = wb.create_sheet(title="总表(空)")
        for j, h in enumerate(header, start=1):
            ws.cell(row=1, column=j, value=h)

    # 有其它表则移除占位；只有占位则把它改名为“总表(空)”
    if len(wb.worksheets) > 1:
        wb.remove(default_ws)
    else:
        default_ws.title = "总表(空)"
        # 写一行表头，避免完全空表
        for j, h in enumerate(header, start=1):
            default_ws.cell(row=1, column=j, value=h)

    # 设置第一个表为活动表
    wb.active = 0

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()



def _export_search_xlsx(*, keywords: str, start: str, end: str,
                        spiders: Optional[List[str]], job_tag: str,
                        created_after: Optional[str] = None,
                        strict: int = 0, kw_logic: str = "OR"):
    data = _fetch_rows(
        keywords=keywords, start=start, end=end,
        spiders=spiders, job_tag=job_tag,
        created_after=created_after, strict=strict, kw_logic=kw_logic
    )
    rows, cols, gk = data["rows"], data["cols"], data["group_key"]
    grouped = defaultdict(list)
    for r in rows:
        grouped[str(r.get(gk, "") or "misc")].append(r)
    content = _write_xlsx(grouped, cols)
    return StreamingResponse(io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _export_board_xlsx(*, start: str, end: str, job_tag: str,
                       boards: Optional[Dict[str, List[str]]] = None,
                       created_after: Optional[str] = None):
    """优先用 job_tag 前缀过滤；否则按 boards 的关键词回退分拣；并追加“总表”"""
    # 1) 先尝试 job_tag 过滤
    data = _fetch_rows(start=start, end=end, job_tag=job_tag, created_after=created_after)
    rows, cols = data["rows"], data["cols"]

    def _extract_board_from_jobtag(jt: str) -> str:
        m = re.search(r"board=([^|]+)", jt or "")
        return m.group(1) if m else ""

    has_jobtag_col = "job_tag" in cols
    grouped = defaultdict(list)
    all_rows_for_total: List[Dict[str, Any]] = []

    if has_jobtag_col and rows:
        # 根据 job_tag 切板块
        for r in rows:
            board = _extract_board_from_jobtag(str(r.get("job_tag") or ""))
            if not board:
                hay = (r.get("title") or "") + "\n" + (r.get("summary") or "") + "\n" + (r.get("source") or "")
                board = "Other" if not hay else "Matched"
            r["_board"] = board
            r["_kw_hit"] = ""
            grouped[board].append(r)
            all_rows_for_total.append(r)
    else:
        # 回退：用 boards 的关键词在 title/summary/source 里做匹配
        boards = boards or {}
        boards_clean: Dict[str, List[str]] = {}
        for b, ks in boards.items():
            arr = [str(x).strip() for x in (ks or []) if str(x).strip()] if isinstance(ks, list) else _split_keywords(str(ks or ""))
            if arr:
                boards_clean[str(b).strip()] = arr

        for r in rows:
            hay = " ".join([str(r.get("title") or ""), str(r.get("summary") or ""), str(r.get("source") or "")])
            assigned_board = ""
            hit_kw = ""
            for b, ks in boards_clean.items():
                for k in ks:
                    if k and k in hay:
                        assigned_board = b
                        hit_kw = k
                        break
                if assigned_board:
                    break
            if not assigned_board:
                assigned_board = "Other"
            r["_board"] = assigned_board
            r["_kw_hit"] = hit_kw
            grouped[assigned_board].append(r)
            all_rows_for_total.append(r)

    # 生成“总表”+ 各板块
    sheets: Dict[str, List[Dict[str, Any]]] = {}
    sheets["总表"] = all_rows_for_total
    for b, rs in grouped.items():
        sheets[b] = rs

    content = _write_xlsx(sheets, cols, extra_cols=["_board", "_kw_hit"])
    return StreamingResponse(io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------------- 路由：导出（不带 /api 前缀！） ----------------------

@router.get("/export.xlsx", name="export_xlsx_get")
def export_excel_get(
    keywords: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    spiders: str = Query(default=""),
    job_tag: str = Query(default=""),
    strict: int = Query(default=0),
    kw_logic: str = Query(default="OR"),
):
    spiders_list = [s.strip() for s in spiders.split(",") if s.strip()] if spiders else None
    return _export_search_xlsx(
        keywords=keywords, start=start, end=end,
        spiders=spiders_list, job_tag=job_tag, strict=strict, kw_logic=kw_logic
    )


@router.post("/export.xlsx", name="export_xlsx_post")
def export_excel_post(
    group_by: Optional[str] = Query(default=None),
    with_crawl: int = Query(default=0),
    job_tag: str = Query(default=""),
    crawl_timeout: int = Query(default=6),
    crawl_concurrency: int = Query(default=4),
    body: Dict[str, Any] = Body(default={}),
):
    # 1) 板块导出：并发调度 -> 等待 -> 导出（仅本次）
    if (group_by or "").lower() == "board":
        date_start = str(body.get("date_start") or body.get("start") or "").strip()
        date_end   = str(body.get("date_end") or body.get("end") or "").strip()
        boards_raw = body.get("boards")
        boards = boards_raw if isinstance(boards_raw, dict) else parse_boards_any(boards_raw, DEFAULT_BOARDS)
        prefix = job_tag or f"board_export_{int(time.time())}"
        t0 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        def _do_schedule():
            last_hours = int(os.getenv("JOURNAL_LAST_HOURS", "0"))  # 默认 0，由日期窗控制
            max_pages  = int(body.get("max_pages") or os.getenv("JOURNAL_MAX_PAGES", "3"))
            spiders_csv = body.get("spiders") or os.getenv("JOURNAL_SPIDERS", "")
            spiders = [s.strip() for s in str(spiders_csv).split(",") if s.strip()] or None
            enable_pc     = os.getenv("JOURNAL_ENABLE_PC", "1")
            enable_mobile = os.getenv("JOURNAL_ENABLE_MOBILE", "1")

            for board, kws in (boards or {}).items():
                if isinstance(kws, str):
                    kws_clean = _split_keywords(kws)
                else:
                    kws_clean = [str(k).strip() for k in (kws or []) if str(k).strip()]
                for kw in kws_clean:
                    job_id = f"{prefix}:{board}:{kw}:{int(time.time()%100000)}"
                    payload = {
                        "keywords": kw,
                        "date_start": date_start,
                        "date_end": date_end,
                        "last_hours": last_hours,
                        "max_pages": max_pages,
                        "enable_pc": enable_pc,
                        "enable_mobile": enable_mobile,
                        "spiders": spiders,
                        "job_tag": f"{prefix}|board={board}|kw={kw}",
                    }
                    try:
                        scrapyd_runner.run_all(job_id, payload)
                    except Exception:
                        pass

        if with_crawl:
            _do_schedule()
            time.sleep(max(0, int(crawl_timeout or 6)))

        return _export_board_xlsx(
            start=date_start, end=date_end, job_tag=prefix,
            boards=boards, created_after=t0
        )

    # 2) 搜索页：with_crawl=1 => 调度 -> 等待 -> 导出（只导本次）
    if with_crawl:
        keywords = str(body.get("keywords") or body.get("kw") or body.get("q") or "").strip()
        spiders_csv = str(body.get("spiders") or "").strip()
        spiders = [s.strip() for s in spiders_csv.split(",") if s.strip()] if spiders_csv else None
        date_start = str(body.get("date_start") or body.get("start") or "").strip()
        date_end   = str(body.get("date_end") or body.get("end") or "").strip()
        strict     = int(body.get("strict") or 0)
        kw_logic   = str(body.get("kw_logic") or "OR")

        jid = job_tag or f"search_export_{int(time.time())}"
        t0 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "keywords": keywords,
            "date_start": date_start,
            "date_end": date_end,
            "last_hours": int(os.getenv("JOURNAL_LAST_HOURS","0")),   # 默认 0
            "max_pages": int(body.get("max_pages") or os.getenv("JOURNAL_MAX_PAGES","3")),
            "enable_pc": os.getenv("JOURNAL_ENABLE_PC","1"),
            "enable_mobile": os.getenv("JOURNAL_ENABLE_MOBILE","1"),
            "spiders": spiders,
            "job_tag": jid,
        }
        try:
            scrapyd_runner.run_all(jid, payload)
        except Exception:
            pass

        time.sleep(max(0, int(crawl_timeout or 15)))
        return _export_search_xlsx(
            keywords=keywords, start=date_start, end=date_end,
            spiders=spiders, job_tag=jid, created_after=t0,
            strict=strict, kw_logic=kw_logic
        )

    # 3) 兼容：非 with_crawl 的 POST => 当 GET 用
    return export_excel_get(
        keywords=str(body.get("keywords") or ""),
        start=str(body.get("start") or body.get("date_start") or ""),
        end=str(body.get("end") or body.get("date_end") or ""),
        spiders=str(body.get("spiders") or ""),
        job_tag=job_tag,
    )

# ---------------------- 路由：诊断 / 预聚合 / Admin 清理（原样保留） ----------------------

try:
    from api.services import journal as journal_svc
except Exception:
    journal_svc = None


@router.get("/_diag/db")
def diag_db(reset: int = Query(default=0)):
    global _DB_CACHE, _TABLE_CACHE
    if reset:
        _DB_CACHE = None; _TABLE_CACHE = None
    conn = _connect()
    table = _detect_table(conn)
    cols = _list_cols(conn, table) if table else []
    cur = conn.cursor() if table else None
    total = 0
    peek = []
    if table:
        try:
            cur.execute(f"SELECT COUNT(1) AS c FROM {table}")
            total = int(cur.fetchone()["c"])
            cur.execute(f"SELECT * FROM {table} ORDER BY ROWID DESC LIMIT 3")
            peek = [dict(r) for r in cur.fetchall()]
        except Exception:
            pass
    conn.close()
    return JSONResponse({
        "db_path": _DB_CACHE,
        "table": table,
        "cols": cols,
        "total_rows": total,
        "peek": peek
    })


@router.get("/journal/list")
def journal_list():
    if not journal_svc:
        return JSONResponse({"ok": False, "message": "journal service not available"})
    return JSONResponse(journal_svc.list_journals())


@router.post("/journal/refresh")
def journal_refresh(date: str = Query(...), boards_json: str = Query(default="")):
    if not journal_svc:
        return JSONResponse({"ok": False, "message": "journal service not available"})
    try:
        boards = json.loads(boards_json) if boards_json else DEFAULT_BOARDS
    except Exception:
        boards = DEFAULT_BOARDS
    return JSONResponse(journal_svc.refresh_journal(date, boards))


@router.post("/journal/delete")
def journal_delete(date: str = Query(...)):
    if not journal_svc:
        return JSONResponse({"ok": False, "message": "journal service not available"})
    return JSONResponse(journal_svc.delete_journal(date))


ADMIN_TOKEN = os.environ.get("PR_ADMIN_TOKEN", "dev-token")


def _check_token(token: str):
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _vacuum(conn: sqlite3.Connection):
    try:
        conn.execute("VACUUM;")
        conn.commit()
    except Exception:
        pass


@router.post("/admin/clean-news")
def admin_clean_news(
    body: Dict[str, Any] = Body(..., example={
        "before_date": "2025-09-10",
        "before_created_at": "2025-09-10 00:00:00",
        "sites": ["dayoo", "nfnews"],
        "spiders": ["dayoo_search", "nfnews_search"],
        "job_tag_prefix": "board_export_",
        "only_test": True
    }),
    token: str = Query(default=""),
):
    _check_token(token)

    conn = _connect()
    table = _detect_table(conn)
    if not table:
        conn.close()
        return JSONResponse({"ok": True, "deleted": 0})

    cols = _list_cols(conn, table)
    where, params = [], []

    before_date = str(body.get("before_date") or "").strip()
    if before_date:
        parts = []
        if "published_at" in cols:
            parts.append("(published_at GLOB '____-__-__*' AND substr(published_at,1,10) < ?)")
        if "date" in cols:
            parts.append("(date GLOB '____-__-__*' AND substr(date,1,10) < ?)")
        if parts:
            where.append("(" + " OR ".join(parts) + ")")
            params.extend([before_date] * len(parts))

    before_created_at = str(body.get("before_created_at") or "").strip()
    if before_created_at and "created_at" in cols:
        where.append("created_at < ?")
        params.append(before_created_at)

    sites = body.get("sites") or []
    spiders = body.get("spiders") or []
    if sites and "site" in cols:
        qs = ",".join(["?"] * len(sites))
        where.append(f"site IN ({qs})")
        params.extend([str(s) for s in sites])
    if spiders and ("spider" in cols or "site" in cols):
        key = "spider" if "spider" in cols else "site"
        qs = ",".join(["?"] * len(spiders))
        where.append(f"{key} IN ({qs})")
        params.extend([str(s) for s in spiders])

    jtp = str(body.get("job_tag_prefix") or "").strip()
    if jtp and "job_tag" in cols:
        where.append("job_tag LIKE ?")
        params.append(jtp + "%")

    if not where:
        conn.close()
        raise HTTPException(status_code=400, detail="No conditions provided; refusing to delete whole table.")

    cur = conn.cursor()
    sql_count = f"SELECT COUNT(1) AS c FROM {table} WHERE " + " AND ".join(where)
    n = int(cur.execute(sql_count, params).fetchone()["c"])

    if body.get("only_test"):
        conn.close()
        return JSONResponse({"ok": True, "would_delete": n})

    cur.execute(f"DELETE FROM {table} WHERE " + " AND ".join(where), params)
    conn.commit()
    _vacuum(conn)
    conn.close()
    return JSONResponse({"ok": True, "deleted": n})


@router.post("/admin/clear-db")
def admin_clear_db(
    sure: int = Query(default=0, description="设为1以确认清空整个表"),
    token: str = Query(default=""),
):
    _check_token(token)
    if not sure:
        raise HTTPException(status_code=400, detail="confirmation 'sure=1' required")

    conn = _connect()
    table = _detect_table(conn)
    if not table:
        conn.close()
        return JSONResponse({"ok": True, "deleted": 0})
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {table}")
    conn.commit()
    _vacuum(conn)
    conn.close()
    return JSONResponse({"ok": True, "deleted": "ALL"})