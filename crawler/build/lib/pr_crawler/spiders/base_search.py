# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import scrapy
from scrapy.http import Response
from parsel import Selector as PSelector


def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()

    m = re.search(r"(\d+)\s*分钟前", s)
    if m:
        return datetime.now() - timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s*小时前", s)
    if m:
        return datetime.now() - timedelta(hours=int(m.group(1)))
    if "昨天" in s:
        return (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "前天" in s:
        return (datetime.now() - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass

    m = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?:\s+\d{2}:\d{2})?", s)
    if m:
        raw = m.group(1).replace("/", "-").replace(".", "-")
        try:
            return datetime.strptime(raw, "%Y-%m-%d")
        except Exception:
            pass
    return None


def fmt_date(dt: Optional[datetime]) -> str:
    return "" if not dt else dt.strftime("%Y-%m-%d")


class NewsItem(scrapy.Item):
    title = scrapy.Field()
    url = scrapy.Field()
    summary = scrapy.Field()
    source = scrapy.Field()
    date = scrapy.Field()
    site = scrapy.Field()
    keywords = scrapy.Field()


class BaseNewsSpider(scrapy.Spider):
    """
    约定：
    - 通过 -a keywords=... 传关键词（self.keywords）
    - 可选 -a date_start=YYYY-MM-DD -a date_end=YYYY-MM-DD 控制日期过滤
    - 子类用 self.make_item(...) 产出 dict；pipelines 会做去重/关键词过滤
    - 调试：self.dump_debug(response, tag=...)
    """
    custom_settings = {
        "COOKIES_ENABLED": True,
        "TELNETCONSOLE_ENABLED": False,
    }

    def __init__(self, keywords: str = "", date_start: str = "", date_end: str = "", **kwargs: Any) -> None:
        super().__init__()
        # 兼容误传的 -a start / -a end（绝不创建 self.start/self.end）
        legacy_start = kwargs.pop("start", "")
        legacy_end = kwargs.pop("end", "")

        # 关键词
        self.keywords: str = (keywords or "").strip()

        # 统一使用 *_raw（避免与 Scrapy 的 start() 名称碰撞）
        raw_start = (date_start or legacy_start or "").strip()
        raw_end = (date_end or legacy_end or "").strip()
        self.start_raw: str = raw_start
        self.end_raw: str = raw_end

        # 后备窗口：仅当未给出显式区间时使用
        try:
            self.last_hours: int = int(kwargs.pop("last_hours", 0) or 0)
        except Exception:
            self.last_hours = 0

        # 解析为 datetime，供子类统一调用
        self.start_dt: Optional[datetime] = parse_date(self.start_raw) if self.start_raw else None
        self.end_dt: Optional[datetime] = parse_date(self.end_raw) if self.end_raw else None

        # 若未指定显式时间窗，则用 last_hours 回退
        if not (self.start_dt or self.end_dt) and self.last_hours > 0:
            now = datetime.now()
            self.start_dt = now - timedelta(hours=self.last_hours)
            self.end_dt = now

        # 保留 **kwargs
        self.extra_kwargs: Dict[str, Any] = dict(kwargs)

        self.logger.info("%s init: kw='%s', range=%s~%s, last_hours=%s",
                         self.name, self.keywords, self.start_raw or "", self.end_raw or "", self.last_hours)

        # 调试目录
        self.debug_dir = os.path.abspath(os.path.join(os.getcwd(), "..", "var", "debug"))
        os.makedirs(self.debug_dir, exist_ok=True)

    # --------- 工具 ---------
    def text_of(self, sel: PSelector) -> str:
        return clean_text("".join(sel.xpath(".//text()").getall()))

    def within_range(self, dt_val: Optional[datetime]) -> bool:
        # 若无时间窗，放行
        if not (self.start_dt or self.end_dt):
            return True
        # 有时间窗但解析不到日期 => 丢弃
        if not dt_val:
            return False
        if self.start_dt and dt_val < self.start_dt:
            return False
        if self.end_dt and dt_val > (self.end_dt + timedelta(days=1) - timedelta(seconds=1)):
            return False
        return True

    def make_item(
        self,
        *,
        title: str,
        url: str,
        summary: str = "",
        source: str = "",
        date: str = "",
        site: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "title": clean_text(title),
            "url": (url or "").strip(),
            "summary": clean_text(summary),
            "source": clean_text(source) or (site or self.name) or "",
            "date": clean_text(date),
            "site": (site or self.name) or "",
            "keywords": self.keywords,
        }
        if extra:
            item.update(extra)
        return item

    def validate(self, item: Dict[str, Any]) -> bool:
        if not item.get("title") or not item.get("url"):
            return False
        from_dt = parse_date(item.get("date", ""))
        # 有时间窗却没有可解析日期，直接丢弃
        if (self.start_dt or self.end_dt) and not from_dt:
            return False
        return self.within_range(from_dt)

    def dump_debug(self, response: Response, tag: str) -> str:
        body = response.text or ""
        h = "%08x" % (abs(hash(body)) & 0xFFFFFFFF)
        ctype = (response.headers.get(b"Content-Type") or b"").decode("utf-8", "ignore").lower()
        ext = "json" if "application/json" in ctype else "html"
        fn = os.path.join(self.debug_dir, f"{self.name}_{tag}_{h}.{ext}")
        with open(fn, "w", encoding="utf-8", errors="ignore") as f:
            f.write(body)
        return fn

    def dump_debug_html(self, html: str, filename: str) -> str:
        debug_dir = os.path.join("var", "debug")
        os.makedirs(debug_dir, exist_ok=True)
        filepath = os.path.join(debug_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        return filepath
