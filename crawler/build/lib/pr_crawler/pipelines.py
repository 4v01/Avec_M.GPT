# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, json, sqlite3, hashlib
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from scrapy import Spider
from scrapy.exceptions import DropItem

# -----------------------
# 小工具
# -----------------------

def _norm(s: Any) -> str:
    if s is None:
        return ""
    if isinstance(s, (dict, list, tuple)):
        try:
            return json.dumps(s, ensure_ascii=False)
        except Exception:
            return str(s)
    return str(s).strip()

def _host_of(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower()
    except Exception:
        return ""

def _sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8")).hexdigest()

def _split_keywords(s: str) -> list[str]:
    if not s:
        return []
    s = s.replace("，", ",").replace("|", ",").replace("、", ",")
    parts = re.split(r"[,\s]+", s.strip())
    return [p for p in (x.strip() for x in parts) if p]

def _contains_any(big: str, keys: Iterable[str]) -> bool:
    big = (big or "").lower()
    for k in keys:
        k = (k or "").lower().strip()
        if k and k in big:
            return True
    return False

# -----------------------
# 1) 规范字段
# -----------------------

class NormalizeFieldsPipeline:
    """
    - 补齐/规范字段：title/url/summary/source/site/spider/keywords/author
    - 计算 url_hash
    - 统一 published_at/date/created_at 的格式为 'YYYY-mm-dd HH:MM:SS' / 'YYYY-mm-dd'
    """
    def process_item(self, item: Dict[str, Any], spider: Spider):
        item["title"]   = _norm(item.get("title"))
        item["url"]     = _norm(item.get("url"))
        item["summary"] = _norm(item.get("summary"))
        item["source"]  = _norm(item.get("source"))
        item["site"]    = _norm(item.get("site")) or _host_of(item["url"])
        item["spider"]  = getattr(spider, "name", _norm(item.get("spider")))
        item["author"]  = _norm(item.get("author"))
        # keywords 支持 str/list
        kws = item.get("keywords")
        if isinstance(kws, str):
            item["keywords"] = ", ".join(_split_keywords(kws))
        elif isinstance(kws, (list, tuple)):
            item["keywords"] = ", ".join([_norm(x) for x in kws if _norm(x)])
        else:
            item["keywords"] = ""

        # url_hash
        item["url_hash"] = item.get("url_hash") or _sha1(item["url"])

        # published_at / date：尽量只清洗为可比较的文本，不做兜底填充
        pub = _norm(item.get("published_at"))
        dat = _norm(item.get("date"))
        # 常见形式只保留前 19/10 位
        if re.match(r"^\d{4}-\d{2}-\d{2}", pub):
            item["published_at"] = pub[:19]
        elif re.match(r"^\d{4}/\d{2}/\d{2}", pub):
            item["published_at"] = pub.replace("/", "-")[:19]
        else:
            item["published_at"] = pub  # 保留原样（导出时再优先使用能识别的）

        if re.match(r"^\d{4}-\d{2}-\d{2}", dat):
            item["date"] = dat[:19]
        elif re.match(r"^\d{4}/\d{2}/\d{2}", dat):
            item["date"] = dat.replace("/", "-")[:19]
        else:
            item["date"] = dat

        # created_at：仅在此刻生成，表示“采集写入时间”
        if not _norm(item.get("created_at")):
            item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # job_tag：透传自 spider.attr 或 item
        jt = _norm(getattr(spider, "job_tag", "") or item.get("job_tag"))
        if jt:
            item["job_tag"] = jt

        # 基本必需项
        if not item["url"] or (not item["title"] and not item["summary"]):
            raise DropItem("missing-url-or-title")
        return item

# -----------------------
# 2) 关键词命中（可选）
# -----------------------

class KeywordMatchPipeline:
    """
    - 读取 item['keywords']（逗号/空格切分）
    - 命中范围：title/summary；若 fallback_body=True，再尝试正文字段（如 content/body/html）
    - 不命中则 DropItem
    """
    def __init__(self, allow_empty: bool = True, fallback_body: bool = True):
        self.allow_empty = allow_empty
        self.fallback_body = fallback_body
        self.body_fields = ("content", "body", "html", "text")

    @classmethod
    def from_crawler(cls, crawler):
        allow_empty = crawler.settings.getbool("KW_ALLOW_EMPTY", True)
        fallback_body = crawler.settings.getbool("KW_FALLBACK_BODY", True)
        return cls(allow_empty=allow_empty, fallback_body=fallback_body)

    def process_item(self, item: Dict[str, Any], spider: Spider):
        kws = _split_keywords(item.get("keywords", ""))
        if not kws:
            if self.allow_empty:
                return item
            raise DropItem("keyword-empty")

        hay = f"{item.get('title','')} {item.get('summary','')}"
        if _contains_any(hay, kws):
            return item

        if self.fallback_body:
            for f in self.body_fields:
                if _contains_any(_norm(item.get(f, "")), kws):
                    return item

        raise DropItem("keyword-not-match")

# -----------------------
# 3) 去重（URL 或 标题）
# -----------------------

class DedupPipeline:
    """
    - 单次运行内用 set 去重
    - 跨运行用 SQLite 查询：库里存在“相同 url_hash”或“相同 title”的直接丢弃
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._seen_hash: set[str] = set()
        self._seen_title: set[str] = set()
        self._conn: Optional[sqlite3.Connection] = None

    @classmethod
    def from_crawler(cls, crawler):
        db = crawler.settings.get("ITEMS_DB_PATH") or \
             os.environ.get("NEWS_DB_PATH") or \
             os.path.join("scripts", "windows", "var", "data", "items.sqlite3")
        os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
        return cls(db)

    def open_spider(self, spider: Spider):
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_table()

    def close_spider(self, spider: Spider):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_table(self):
        cur = self._conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash     TEXT,
            title        TEXT,
            url          TEXT,
            summary      TEXT,
            source       TEXT,
            date         TEXT,
            published_at TEXT,
            site         TEXT,
            spider       TEXT,
            author       TEXT,
            keywords     TEXT,
            job_tag      TEXT,
            created_at   TEXT
        )
        """)
        # 用于跨运行去重：hash、title 建索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_url_hash ON news(url_hash)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_title    ON news(title)")
        self._conn.commit()

    def _exists_in_db(self, url_hash: str, title: str) -> bool:
        cur = self._conn.cursor()
        if url_hash:
            cur.execute("SELECT 1 FROM news WHERE url_hash=? LIMIT 1", (url_hash,))
            if cur.fetchone():
                return True
        if title:
            cur.execute("SELECT 1 FROM news WHERE title=? LIMIT 1", (title,))
            if cur.fetchone():
                return True
        return False

    def process_item(self, item: Dict[str, Any], spider: Spider):
        uh = _norm(item.get("url_hash"))
        tt = _norm(item.get("title"))
        if uh in self._seen_hash or tt in self._seen_title:
            raise DropItem("dup-memory")
        if self._exists_in_db(uh, tt):
            raise DropItem("dup-db")
        self._seen_hash.add(uh)
        self._seen_title.add(tt)
        return item

# -----------------------
# 4) 落库
# -----------------------

class SQLiteStorePipeline:
    """
    - 自动建表（与 DedupPipeline 一致）
    - INSERT
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @classmethod
    def from_crawler(cls, crawler):
        db = crawler.settings.get("ITEMS_DB_PATH") or \
             os.environ.get("NEWS_DB_PATH") or \
             os.path.join("scripts", "windows", "var", "data", "items.sqlite3")
        os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
        return cls(db)

    def open_spider(self, spider: Spider):
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_table()

    def close_spider(self, spider: Spider):
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def _ensure_table(self):
        cur = self._conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash     TEXT,
            title        TEXT,
            url          TEXT,
            summary      TEXT,
            source       TEXT,
            date         TEXT,
            published_at TEXT,
            site         TEXT,
            spider       TEXT,
            author       TEXT,
            keywords     TEXT,
            job_tag      TEXT,
            created_at   TEXT
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_url_hash ON news(url_hash)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_title    ON news(title)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_created  ON news(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_news_pubdate  ON news(published_at)")
        self._conn.commit()

    def process_item(self, item: Dict[str, Any], spider: Spider):
        cur = self._conn.cursor()
        cur.execute("""
        INSERT INTO news
        (url_hash, title, url, summary, source, date, published_at, site, spider, author, keywords, job_tag, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            _norm(item.get("url_hash")),
            _norm(item.get("title")),
            _norm(item.get("url")),
            _norm(item.get("summary")),
            _norm(item.get("source")),
            _norm(item.get("date")),
            _norm(item.get("published_at")),
            _norm(item.get("site")),
            _norm(item.get("spider")),
            _norm(item.get("author")),
            _norm(item.get("keywords")),
            _norm(item.get("job_tag")),
            _norm(item.get("created_at")),
        ))
        return item
