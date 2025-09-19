from __future__ import annotations
import sqlite3, re, json
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

# 与 exporter 共享 DB 路径探测（只读取其值即可）
from api.services.exporter import DB_PATH  # type: ignore

# ----------------------- 基础 -----------------------

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _is_valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except Exception:
        return False


def _safe_tbl(date: str) -> str:
    # 强制 journal_YYYY-MM-DD
    if not _is_valid_date(date):
        raise ValueError("bad date")
    return f"journal_{date}"


# ----------------------- API 后端逻辑 -----------------------

def list_journals() -> List[Dict[str, Any]]:
    """列出所有 journal_YYYY-MM-DD 表。"""
    with _conn() as cx:
        rows = cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'journal_%' ORDER BY name DESC"
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            name = r[0] if not isinstance(r, sqlite3.Row) else r["name"]
            m = re.match(r"^journal_(\d{4}-\d{2}-\d{2})$", name)
            if not m:
                continue
            d = m.group(1)
            cnt = cx.execute(f"SELECT COUNT(1) AS c FROM {name}").fetchone()[0]
            out.append({"date": d, "rows": int(cnt)})
        return out


def delete_journal(date: str) -> Dict[str, Any]:
    tbl = _safe_tbl(date)
    with _conn() as cx:
        cx.execute(f"DROP TABLE IF EXISTS {tbl}")
        cx.commit()
    return {"ok": True, "dropped": tbl}


def refresh_journal(date: str, boards: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    预聚合：按板块将指定日期的数据写入 journal_DATE。
    - 使用 title/summary/source 做 LIKE 匹配（OR 关系）。
    - boards: {board: [kw1, kw2] | 'kw1|kw2'}；为空则返回错误（由前端传）。
    - 统一 schema：原表 * + board 列。
    """
    tbl = _safe_tbl(date)
    if not boards:
        return {"ok": False, "message": "boards required"}

    # 解析 boards -> {board: [kw, ...]}
    def norm(b: Dict[str, Any]) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for k, v in (b or {}).items():
            if isinstance(v, list):
                arr = [str(x).strip() for x in v if str(x).strip()]
            else:
                arr = [s.strip() for s in str(v).replace("，", ",").replace("|", ",").split(",") if s.strip()]
            if arr:
                out[str(k).strip() or "未命名板块"] = arr
        return out

    boards_norm = norm(boards)

    with _conn() as cx:
        # 检测底表名
        names = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        base_table = "news" if "news" in names else ("items" if "items" in names else None)
        if not base_table:
            return {"ok": True, "created": tbl, "rows": 0}

        # 统一列集合
        cols = [r[1] for r in cx.execute(f"PRAGMA table_info({base_table})").fetchall()]
        pub_col = "published_at" if "published_at" in cols else ("date" if "date" in cols else None)

        # 重建 journal 表
        cx.execute(f"DROP TABLE IF EXISTS {tbl}")
        cx.execute(
            f"CREATE TABLE {tbl} AS SELECT *, '' AS board FROM {base_table} WHERE 1=0"
        )
        cx.execute(f"CREATE INDEX IF NOT EXISTS idx_{tbl}_date ON {tbl}({pub_col})") if pub_col else None

        inserted = 0
        # 从底表读出候选行（限制当天，减少扫描量）
        if pub_col:
            rs = cx.execute(
                f"SELECT * FROM {base_table} WHERE {pub_col} = ?", (date,)
            ).fetchall()
        else:
            rs = cx.execute(f"SELECT * FROM {base_table}").fetchall()

        for row in rs:
            d = dict(row)
            hay = (d.get("title") or "") + "\n" + (d.get("summary") or "") + "\n" + (d.get("source") or "")
            hit_boards = [b for b, kws in boards_norm.items() if any(kw in hay for kw in kws)]
            for b in hit_boards:
                d2 = dict(d)
                d2["board"] = b
                # 插入——列名对齐
                ks = ",".join(d2.keys())
                qs = ",".join(["?"] * len(d2))
                cx.execute(f"INSERT INTO {tbl}({ks}) VALUES({qs})", list(d2.values()))
                inserted += 1

        cx.commit()
        return {"ok": True, "created": tbl, "rows": inserted}