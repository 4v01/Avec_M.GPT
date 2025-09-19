# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import urllib.parse
from typing import Dict, Optional, List
from urllib.parse import urlparse

import scrapy
from scrapy import Request
from .base_search import BaseNewsSpider, clean_text, parse_date, fmt_date  # noqa: F401


SEARX_DEFAULT = "https://searx.bndkt.io"


class NfnewsSearchSpider(BaseNewsSpider):
    """
    使用 SearXNG 的 HTML 结果页进行“关键词 + 站点”检索：
    - 仅用 GET（不使用 RSS、不使用 categories）
    - 自动根据 start/end 推导 time_range: ["", "day", "week", "month", "year"]
    - 列表页抽取 -> 详情页抽取标题/时间/来源/正文
    - 通过 BaseNewsSpider.validate() 再次基于起止时间过滤，终端让编辑手动看 date 字段
    """
    name = "nfnews_search"
    allowed_domains = [
        "nfnews.com", "www.nfnews.com", "epaper.nfnews.com", "static.nfnews.com"
    ]

    custom_settings = {
        **BaseNewsSpider.custom_settings,
        "CONCURRENT_REQUESTS": 8,
        "DOWNLOAD_DELAY": 0.35,
        "RETRY_TIMES": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://searx.space/",
        },
    }

    def __init__(
        self,
        keywords: str = "",
        start: str = "",
        end: str = "",
        max_pages: int = 1,
        searx_url: str = "",
        dump_debug: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(keywords=keywords, start=start, end=end, **kwargs)
        self.max_pages = int(max_pages or 1)
        self.dump_debug_flag = int(dump_debug or 0)
        self.site_tag = "nfnews"
        # SearX 实例优先用入参，否则用默认
        self.searx_url = (searx_url or SEARX_DEFAULT).rstrip("/")
        # 基于 start/end 推时间档
        self.time_range = self._pick_time_range()
        self.logger.info("%s init: kw='%s', pages=%s, time_range='%s'", self.name, self.keywords, self.max_pages, self.time_range)

    # ---------- 时间档映射 ----------
    def _pick_time_range(self) -> str:
        """
        SearXNG 支持: "", "day", "week", "month", "year"
        约定：只看 start_dt 与“现在”的距离来选择粒度；若无 start/end，返回 ""（不限）
        """
        if not (self.start_dt or self.end_dt):
            return ""
        # 把 None 的情况兜底为“尽量不限制”，同时留给 validate 再过滤
        from datetime import datetime, timedelta
        now = datetime.now()
        start_dt = self.start_dt or (self.end_dt and (self.end_dt - timedelta(days=365))) or None
        if not start_dt:
            return ""
        delta = (now - start_dt).days
        if delta <= 1:
            return "day"
        if delta <= 7:
            return "week"
        if delta <= 31:
            return "month"
        if delta <= 366:
            return "year"
        return ""  # 超一年：不限定，让 validate 去筛

    # ---------- 入口 ----------
    def start_requests(self):
        if not self.keywords:
            self.logger.warning("未提供关键词，直接结束。")
            return
        q = f"{self.keywords} site:nfnews.com"
        url = self._build_search_url(q, pageno=1)
        self.logger.info("HTML GET：%s", url)
        yield Request(url, callback=self.parse_list, meta={"q": q, "page": 1}, dont_filter=True)

    def _build_search_url(self, q: str, pageno: int) -> str:
        params = {
            "q": q,
            "language": "all",
            "safesearch": "0",
            "pageno": str(pageno),
        }
        if self.time_range in {"day", "week", "month", "year"}:
            params["time_range"] = self.time_range
        qs = urllib.parse.urlencode(params, safe="+")
        return f"{self.searx_url}/search?{qs}"

    # ---------- 列表页 ----------
    def parse_list(self, response: scrapy.http.Response):
        page = int(response.meta.get("page", 1))
        q = response.meta.get("q", "")
        if self.dump_debug_flag:
            self.dump_debug(response, f"searx_list_p{page}")

        sel = response.selector
        # searx simple theme 的通用结果块
        nodes = sel.xpath("//article[contains(@class,'result') and .//h3/a[@href]]")
        count = len(nodes)

        # 调试记录（用于观察翻页召回）
        yield {
            "type": "pagination_info",
            "page": page,
            "query": self.keywords,
            "results_count": count,
            "url": response.url,
            "site": self.site_tag,
            "backend": "searx_html",
            "time_range": self.time_range or "",
        }

        for idx, r in enumerate(nodes, start=1):
            # 标题与链接
            title = clean_text("".join(r.xpath(".//h3/a//text()").getall()))
            href = (r.xpath(".//h3/a/@href").get() or "").strip()
            if not href:
                continue
            # 只收 nfnews 域
            host = (urlparse(href).hostname or "").lower()
            if not (host == "nfnews.com" or host == "www.nfnews.com" or host.endswith(".nfnews.com")):
                continue

            # 列表摘要/可能的日期（不强依赖，详情页再提）
            snippet = clean_text("".join(r.xpath(".//p[contains(@class,'content')]//text()").getall()))
            # 从摘要文本里粗抽日期（ searx often shows like "3. 3. 2025 — ..."）
            raw_date = ""
            txt = snippet
            m1 = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*\d{4})", txt)
            m2 = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2})", txt)
            if m1:
                # 3. 3. 2025 -> 2025-03-03
                parts = re.split(r"\D+", m1.group(1))
                if len(parts) >= 3:
                    y = parts[-1]; m = parts[-3] if len(parts) >= 3 else "1"; d = parts[-2]
                    raw_date = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            elif m2:
                raw_date = m2.group(1)

            meta = {
                "seed_title": title,
                "seed_snippet": snippet,
                "seed_date": raw_date,
                "seed_rank": idx,
            }
            yield Request(href, callback=self.parse_article, errback=self.err_article, meta=meta, dont_filter=True)

        # 翻页
        if count > 0 and page < self.max_pages:
            next_url = self._build_search_url(q, pageno=page + 1)
            yield Request(next_url, callback=self.parse_list, meta={"q": q, "page": page + 1}, dont_filter=True)

    # ---------- 详情页 ----------
    def parse_article(self, response: scrapy.http.Response):
        if self.dump_debug_flag:
            host = urlparse(response.url).hostname or "page"
            self.dump_debug(response, f"detail_{host}")

        seed_title = response.meta.get("seed_title") or ""
        seed_snippet = response.meta.get("seed_snippet") or ""
        seed_date = response.meta.get("seed_date") or ""

        title = self._extract_title(response) or seed_title or response.url
        date_str = self._extract_date(response) or seed_date
        source = self._extract_source(response)
        content = self._extract_content(response)

        summary = seed_snippet or clean_text(content[:120])

        item = self.make_item(
            title=title,
            url=response.url,
            summary=summary,
            source=source or self.site_tag,
            date=clean_text(date_str),
            site=self.site_tag,
            extra={
                "content": content,
                "domain": urlparse(response.url).hostname or "",
            },
        )
        if self.validate(item):
            yield item

    def err_article(self, failure):
        req = getattr(failure, "request", None)
        url = req.url if req else ""
        self.logger.warning("正文抓取失败：%s -> %s", failure.value.__class__.__name__, url)

    # ---------- 站内抽取 ----------
    def _extract_title(self, response: scrapy.http.Response) -> str:
        xp = [
            "//meta[@property='og:title']/@content",
            "//meta[@name='twitter:title']/@content",
            "//h1/text()",
            "//h1//text()",
            "//div[contains(@class,'title')]/h1//text()",
            "//title/text()",
        ]
        for p in xp:
            v = response.xpath(p).get()
            if v:
                v = clean_text(v)
                if p == "//title/text()":
                    v = re.sub(r"\s*[-_｜|].*$", "", v)
                if v:
                    return v
        return ""

    def _extract_date(self, response: scrapy.http.Response) -> str:
        xp = [
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='pubdate']/@content",
            "//meta[@name='publishdate']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@name='date']/@content",
        ]
        for p in xp:
            v = response.xpath(p).get()
            if v:
                return clean_text(v)

        xp2 = [
            "//*[contains(@class,'time')]/text()",
            "//*[contains(@class,'date')]/text()",
            "//*[contains(text(),'发布时间')]/following::text()[1]",
            "//*[contains(text(),'发表时间')]/following::text()[1]",
        ]
        for p in xp2:
            v = clean_text("".join(response.xpath(p).getall()))
            m = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}(?:\s+\d{2}:\d{2})?)", v)
            if m:
                return m.group(1)

        m = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}(?:[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?)", response.text)
        return m.group(1) if m else ""

    def _extract_source(self, response: scrapy.http.Response) -> str:
        v = response.xpath("//meta[@property='og:site_name']/@content").get()
        if v:
            return clean_text(v)
        txt = clean_text(" ".join(response.xpath("//*[contains(text(),'来源')]/text()").getall()))
        m = re.search(r"来源[:：]\s*([^\s|｜]+)", txt)
        if m:
            return clean_text(m.group(1))
        host = urlparse(response.url).hostname or ""
        if host.endswith("nfnews.com"):
            return "南方+ / nfnews"
        return host

    def _extract_content(self, response: scrapy.http.Response) -> str:
        containers = response.xpath(
            "//article | //div[@id='content' or @id='ContentBody' or "
            "contains(@class,'content') or contains(@class,'article') or "
            "contains(@class,'article-content') or contains(@class,'detail') or "
            "contains(@class,'text')]"
        )
        texts: List[str] = []
        if containers:
            for c in containers:
                p_text = c.xpath(".//p//text()").getall()
                texts.extend(p_text if p_text else c.xpath(".//text()").getall())
        else:
            texts = response.xpath("//body//p//text()").getall() or response.xpath("//body//text()").getall()

        content = clean_text("".join(texts))
        content = re.sub(r"(责任编辑|责编|原标题)[:：].*$", "", content)
        return content.strip()
