# -*- coding: utf-8 -*-
from __future__ import annotations

"""
SQLite pipelines 方案（对齐你现有字段 & 方法名，不改外部接口）：

- NormalizeFieldsPipeline：保持你现有逻辑；
- KeywordMatchPipeline / DedupPipeline：只做本次 job 内的软过滤与去重；
- SQLiteStorePipeline：**仅写入“本次会话库（session DB）”**，爬虫结束后再合并到“主库（master DB）”；

目的：
1) 解决“同一关键词越爬越少”的副作用（历史去重污染实时导出）；
2) 导出 Excel 时，with_crawl=1 直接读取 session DB（由 job_tag / JOB_TAG 决定）；
3) 主库保留为长期积累（可做情感分析、训练集等）。

默认位置：
- 主库（master）：scripts/windows/var/data/items.sqlite3 （与你现状一致）
- 会话库（session）：scripts/windows/var/sessions/{job_tag}/items.sqlite3

你可以通过环境变量覆盖：
- PR_RUNTIME_DIR：运行目录根（默认 cwd）
- PR_MASTER_DB_PATH：主库绝对/相对路径
- PR_SESSION_ROOT：会话库根目录
- JOB_TAG：若 scrapyd_runner 没有传，可临时从环境取

注意：
- **坚决不再用 created_at 兜底 published_at/date**，过滤与排序都优先用 published_at/date；
- 与你当前 items/news 表字段对齐：
    id, url_hash, title, url, summary, source, date, published_at, site, spider,
    author, keywords, job_tag, created_at, mode, page_no, pos
- 如果入库的 item 缺少上述字段，自动补空字符串。
"""

import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from scrapy.exceptions import DropItem

# ----------------------------- 工具函数 -----------------------------

PREFERRED_COLS = [
    "id","url_hash","title","url","summary","source","date","published_at",
    "site","spider","author","keywords","job_tag","created_at","mode","page_no","pos",
]


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _norm(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    # 统一空白
    s = re.sub(r"\s+", " ", s)
    return s


def _ensure_dir(p: str) -> None:
    d = os.path.dirname(p)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


# ----------------------------- 管道一：字段规整 -----------------------------

class NormalizeFieldsPipeline:
    """与现有实现等价：规范字符串、清理多余空白，不做兜底日期。"""

    def process_item(self, item, spider):  # type: ignore[override]
        for k in list(item.keys()):
            if k in ("mode", "page_no", "pos"):
                # 数字类尽量保持原样
                continue
            if isinstance(item[k], (str, bytes, int, float)) or item[k] is None:
                item[k] = _norm(item[k])
        # job_tag：从 spider 或环境兜一下，方便后续入库与导出
        if not item.get("job_tag"):
            job_tag = getattr(spider, "job_tag", None) or getattr(spider, "jobid", None) or os.getenv("JOB_TAG", "")
            if job_tag:
                item["job_tag"] = str(job_tag)
        # created_at：真实采集时间戳
        if not item.get("created_at"):
            item["created_at"] = _now_str()
        return item


# ----------------------------- 管道二：关键词软过滤（与现状对齐） -----------------------------

class KeywordMatchPipeline:
    """
    仅做“标题/摘要/可选正文字段”匹配；不使用 created_at 作为任何日期兜底。
    """

    key_field = "keywords"
    body_fields = ("content", "body", "text")
    allow_empty = True
    fallback_body = True

    def process_item(self, item, spider):  # type: ignore[override]
        raw_kw = _norm(item.get(self.key_field, ""))
        kws = self._split_keywords(raw_kw)
        if not kws:
            if self.allow_empty:
                return item
            raise DropItem("keyword-empty")

        title = _norm(item.get("title", ""))
        summary = _norm(item.get("summary", ""))
        if self._contains_any(title + " " + summary, kws):
            return item

        if self.fallback_body:
            for f in self.body_fields:
                if self._contains_any(_norm(item.get(f, "")), kws):
                    return item

        raise DropItem("keyword-not-match")

    # --- helpers ---
    @staticmethod
    def _split_keywords(s: str) -> list[str]:
        if not s:
            return []
        s = s.replace("，", ",").replace("|", ",").replace("、", ",")
        parts = re.split(r"[,\s]+", s.strip())
        return [p for p in parts if p]

    @staticmethod
    def _contains_any(text: str, kws: Iterable[str]) -> bool:
        t = text.lower()
        for k in kws:
            if k and k.lower() in t:
                return True
        return False


# ----------------------------- 管道三：去重（仅本次 job 内） -----------------------------

class DedupPipeline:
    """
    只在当前 job 内去重（使用 url_hash / url）。
    不触碰历史库，避免“越爬越少”。
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def process_item(self, item, spider):  # type: ignore[override]
        h = _norm(item.get("url_hash")) or _norm(item.get("url"))
        if not h:
            raise DropItem("missing-url-or-title")
        if h in self._seen:
            raise DropItem("dup-memory")
        self._seen.add(h)
        return item


# ----------------------------- 管道四：SQLite 存储（session -> master 合并） -----------------------------

class SQLiteStorePipeline:
    """将数据先写入会话库（session），收尾再合并到主库（master）。"""

    def __init__(self, master_path: str, session_path: str | None) -> None:
        self.master_path = master_path
        self.session_path = session_path or master_path
        self.session_conn: Optional[sqlite3.Connection] = None
        self._cols = list(PREFERRED_COLS)

    @classmethod
    def from_crawler(cls, crawler):  # type: ignore[override]
        settings = crawler.settings

        runtime_dir = os.getenv("PR_RUNTIME_DIR") or os.getcwd()
        master_default = os.path.join(runtime_dir, "scripts", "windows", "var", "data", "items.sqlite3")
        master_path = os.getenv("PR_MASTER_DB_PATH") or settings.get("SQLITE_DB_PATH") or master_default

        # 会话库：优先用 JOB_TAG（spider/job）
        job_tag = (getattr(crawler, "jobid", None) or settings.get("JOB_TAG")
                   or os.getenv("JOB_TAG") or "")
        session_root = os.getenv("PR_SESSION_ROOT") or os.path.join(runtime_dir, "scripts", "windows", "var", "sessions")
        session_path = None
        if job_tag:
            session_path = os.path.join(session_root, job_tag, "items.sqlite3")
        return cls(master_path=master_path, session_path=session_path)

    # --- DB helpers ---
    def _connect_session(self) -> None:
        _ensure_dir(self.session_path)
        self.session_conn = sqlite3.connect(self.session_path)
        self.session_conn.row_factory = sqlite3.Row
        self._ensure_schema(self.session_conn)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        cur = conn.cursor()
        # 统一使用 items 表名作为会话表，主库统一用 news 表名（和你现有导出保持兼容）
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash TEXT,
                title TEXT,
                url TEXT,
                summary TEXT,
                source TEXT,
                date TEXT,
                published_at TEXT,
                site TEXT,
                spider TEXT,
                author TEXT,
                keywords TEXT,
                job_tag TEXT,
                created_at TEXT,
                mode TEXT,
                page_no INTEGER,
                pos INTEGER
            )
            """
        )
        conn.commit()

    def open_spider(self, spider):  # type: ignore[override]
        self._connect_session()

    def close_spider(self, spider):  # type: ignore[override]
        if not self.session_conn:
            return
        try:
            self.session_conn.commit()
        finally:
            self.session_conn.close()
            self.session_conn = None
        # 会话 -> 主库 合并
        try:
            self._merge_session_to_master()
        except Exception as e:
            # 不让异常中断 scrapyd，记录即可
            print(f"[SQLiteStorePipeline] merge error: {e}")

    def process_item(self, item, spider):  # type: ignore[override]
        if not self.session_conn:
            self._connect_session()
        row = {c: _norm(item.get(c, "")) for c in self._cols}
        # 数字列保持类型
        for k in ("page_no", "pos"):
            v = item.get(k)
            if isinstance(v, int):
                row[k] = v
            elif isinstance(v, str) and v.isdigit():
                row[k] = int(v)
            else:
                row[k] = None
        # created_at 最后兜一下时间戳
        row["created_at"] = row.get("created_at") or _now_str()

        cur = self.session_conn.cursor()  # type: ignore[union-attr]
        placeholders = ",".join([":" + c for c in self._cols])
        cur.execute(f"INSERT INTO items ({','.join(self._cols)}) VALUES ({placeholders})", row)
        self.session_conn.commit()  # type: ignore[union-attr]
        return item

    # --- 合并逻辑 ---
    def _merge_session_to_master(self) -> None:
        if not self.session_path:
            return
        _ensure_dir(self.master_path)
        mconn = sqlite3.connect(self.master_path)
        mconn.row_factory = sqlite3.Row
        cur = mconn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash TEXT UNIQUE,
                title TEXT,
                url TEXT,
                summary TEXT,
                source TEXT,
                date TEXT,
                published_at TEXT,
                site TEXT,
                spider TEXT,
                author TEXT,
                keywords TEXT,
                job_tag TEXT,
                created_at TEXT,
                mode TEXT,
                page_no INTEGER,
                pos INTEGER
            )
            """
        )
        # 去重条件以 url_hash 为准；若为空，则退回 url
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_hash ON news(url_hash)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_url ON news(url)")
        mconn.commit()

        # ATTACH 会话库，做一次性去重合并
        cur.execute("ATTACH DATABASE ? AS ses", (self.session_path,))
        # 有些入库没有 url_hash，就用 url 的 sha1 你那边已有计算；这里以 url_hash 优先
        cur.execute(
            """
            INSERT OR IGNORE INTO news (
                url_hash,title,url,summary,source,date,published_at,site,spider,
                author,keywords,job_tag,created_at,mode,page_no,pos
            )
            SELECT s.url_hash, s.title, s.url, s.summary, s.source, s.date, s.published_at, s.site, s.spider,
                   s.author, s.keywords, s.job_tag, s.created_at, s.mode, s.page_no, s.pos
            FROM ses.items AS s
            WHERE COALESCE(s.url_hash, '') <> ''
            """
        )
        # 对于 url_hash 为空但 url 存在的，再按 url 去重一次
        cur.execute(
            """
            INSERT INTO news (
                url_hash,title,url,summary,source,date,published_at,site,spider,
                author,keywords,job_tag,created_at,mode,page_no,pos
            )
            SELECT NULL, s.title, s.url, s.summary, s.source, s.date, s.published_at, s.site, s.spider,
                   s.author, s.keywords, s.job_tag, s.created_at, s.mode, s.page_no, s.pos
            FROM ses.items AS s
            WHERE (s.url_hash IS NULL OR s.url_hash = '')
              AND COALESCE(s.url, '') <> ''
              AND NOT EXISTS (
                    SELECT 1 FROM news n WHERE COALESCE(n.url,'') <> '' AND n.url = s.url
              )
            """
        )
        mconn.commit()
        cur.execute("DETACH DATABASE ses")
        mconn.close()


# ----------------------------- Scrapy 设置入口 -----------------------------
# settings.py 中保持：
# ITEM_PIPELINES = {
#   'pr_crawler.pipelines.NormalizeFieldsPipeline': 100,
#   'pr_crawler.pipelines.KeywordMatchPipeline': 130,
#   'pr_crawler.pipelines.DedupPipeline': 150,
#   'pr_crawler.pipelines.SQLiteStorePipeline': 500,
# }
# 同时可选：设置 SQLITE_DB_PATH / JOB_TAG，或走环境变量。
