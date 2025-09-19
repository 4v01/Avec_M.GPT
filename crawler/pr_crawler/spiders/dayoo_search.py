# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import urllib.parse
from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qs, unquote

import scrapy
from scrapy import Request, FormRequest

from .base_search import BaseNewsSpider, clean_text, parse_date  # :contentReference[oaicite:2]{index=2}


class DayooSearchSpider(BaseNewsSpider):
    """
    大洋网（dayoo.com）基于 SearXNG 的“关键词 + 时间范围”搜索式抓取
    设计要点：
    1) 不使用 categories/news 与 format=rss —— 避免 0 items 与 RSS 不可读问题；
    2) time_range：支持手动传入（-a time_range=day|week|month|year|""），若未传则按 start/end 映射到 SearXNG 的 5 档（不限/天/周/月/年）；
    3) 翻页：POST 翻页（适配 SearXNG Simple 主题的 form 翻页）和 GET pageno 双模式自动识别；
    4) 详情页抽取：沿用 nfnews 的稳健 XPath 与兜底正则（标题/时间/来源/正文），并针对 dayoo 站点名修正来源。
    """
    name = "dayoo_search"
    allowed_domains = [
        # SearXNG 反向代理/实例域名不做强限制，交给 OffsiteMiddleware
        "dayoo.com", "www.dayoo.com", "epaper.dayoo.com",  # 目标域
    ]

    custom_settings = {
        **BaseNewsSpider.custom_settings,  # :contentReference[oaicite:3]{index=3}
        "CONCURRENT_REQUESTS": 8,
        "DOWNLOAD_DELAY": 0.35,
        "RETRY_TIMES": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/123.0.0.0 Safari/537.36"),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://searx.space/",
        },
    }

    def __init__(
        self,
        keywords: str = "",
        start: str = "",
        end: str = "",
        searx_url: str = "",
        time_range: str = "",
        max_pages: int = 1,
        dump_debug: int = 0,
        **kwargs
    ):
        super().__init__(keywords=keywords, start=start, end=end, **kwargs)
        self.dump_debug_flag = int(dump_debug or 0)
        self.site_tag = "dayoo"
        self.max_pages = int(max_pages or 1)

        # 实例池：支持显式传入一个；否则使用内置的一个可靠实例（可自行扩展为轮询列表或动态从 searx.space 拉取）
        self.searx_pool: List[str] = []
        if searx_url:
            self.searx_pool.append(searx_url.rstrip("/"))
        else:
            self.searx_pool.append("https://searx.bndkt.io")  # 你已验证可直连的实例

        # time_range：如未显式给定则按 start/end 推断；值域："" | "day" | "week" | "month" | "year"
        self.time_range_arg = (time_range or "").strip()
        self.logger.info("%s init: kw='%s', pages=%s, time_range='%s'", self.name, self.keywords, self.max_pages, self.time_range_arg or "~")

    # ========== 入口 ==========
    def start_requests(self):
        if not self.keywords:
            self.logger.warning("未提供关键词，直接结束。")
            return

        tr = self._resolve_time_range()  # "" | day | week | month | year
        q = f"{self.keywords} site:dayoo.com"
        for base in self.searx_pool:
            url = f"{base}/search"
            params = {
                "q": q,
                "language": "all",
                "safesearch": "0",
                "pageno": "1",
            }
            if tr:
                params["time_range"] = tr
            full_url = url + "?" + urllib.parse.urlencode(params, safe="")
            self.logger.info("HTML GET：%s", full_url)
            meta = {"page": 1, "q": q, "searx_base": base, "time_range": tr}
            yield Request(full_url, callback=self.parse_list, dont_filter=True, meta=meta)

    # ========== 列表解析 ==========
    def parse_list(self, response: scrapy.http.Response):
        page = int(response.meta.get("page", 1))
        q = response.meta.get("q", "")
        base = response.meta.get("searx_base", "")
        tr = response.meta.get("time_range", "")

        if self.dump_debug_flag:
            self.dump_debug(response, f"list_p{page}")  # :contentReference[oaicite:4]{index=4}

        sel = response.selector
        # 兼容 simple 主题：<article class="result ..."><a class="url_header" ...><h3><a ...>标题</a></h3>...
        nodes = sel.xpath("//article[contains(@class,'result') and .//h3/a[@href]]")
        count = len(nodes)

        # 调试记录一条 pagination_info（与 nfnews_search 同风格，便于链路观察） :contentReference[oaicite:5]{index=5}
        yield {
            "type": "pagination_info",
            "page": page,
            "query": self.keywords,
            "results_count": count,
            "url": response.url,
            "site": self.site_tag,
            "backend": "searxng_html",
            "total_results": count,
        }

        # 逐条处理
        for idx, r in enumerate(nodes, start=1):
            title = clean_text("".join(r.xpath(".//h3//a//text()").getall()))
            href = (r.xpath(".//h3/a/@href").get() or "").strip()
            if not href:
                continue

            # SearXNG 可能返回非 dayoo 域：只保留 dayoo.com / 子域
            try:
                real_url = self._normalize_searx_href(href)
            except Exception:
                real_url = href
            host = (urlparse(real_url).hostname or "").lower()
            if not (host == "dayoo.com" or host == "www.dayoo.com" or host.endswith(".dayoo.com")):
                continue

            snippet = clean_text("".join(r.xpath(".//p[contains(@class,'content')]//text()").getall()))
            # 结果块上经常带日期片段，尝试从可见文本里抓取
            result_text = clean_text(" ".join(r.xpath(".//text()").getall()))
            seed_date = ""
            m = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}(?:\s+\d{2}:\d{2})?)", result_text)
            if m:
                seed_date = m.group(1)

            meta = {
                "seed_title": title,
                "seed_snippet": snippet,
                "seed_date": seed_date,
                "seed_rank": idx,
            }
            yield Request(real_url, callback=self.parse_article, errback=self.err_article, meta=meta, dont_filter=True)

        # 翻页：优先 simple 主题的 POST form；否则尝试 pageno=page+1 GET
        if page < self.max_pages:
            next_form = sel.xpath("//form[@method='post' and @action='/search' and .//input[@name='q']]")
            if next_form:
                formdata: Dict[str, str] = {}
                for inp in next_form.xpath(".//input[@type='hidden' or @type='submit' or @type='text' or @type='search']"):
                    k = inp.xpath("./@name").get()
                    v = inp.xpath("./@value").get() or ""
                    if k and v:
                        formdata[k] = v
                # 强制下一页
                formdata["pageno"] = str(page + 1)
                if tr:
                    formdata["time_range"] = tr
                meta2 = {"page": page + 1, "q": q, "searx_base": base, "time_range": tr}
                yield FormRequest(url=f"{base}/search", formdata=formdata, method="POST", callback=self.parse_list, meta=meta2, dont_filter=True)
            else:
                params = {
                    "q": q, "language": "all", "safesearch": "0", "pageno": str(page + 1)
                }
                if tr:
                    params["time_range"] = tr
                next_url = f"{base}/search?" + urllib.parse.urlencode(params, safe="")
                meta2 = {"page": page + 1, "q": q, "searx_base": base, "time_range": tr}
                yield Request(next_url, callback=self.parse_list, meta=meta2, dont_filter=True)

    # ========== 正文解析 ==========
    def parse_article(self, response: scrapy.http.Response):
        if self.dump_debug_flag:
            host = urlparse(response.url).hostname or "page"
            self.dump_debug(response, f"detail_{host}")  # :contentReference[oaicite:6]{index=6}

        seed_title = response.meta.get("seed_title") or ""
        seed_snippet = response.meta.get("seed_snippet") or ""
        seed_date = response.meta.get("seed_date") or ""

        title = self._extract_title(response) or seed_title or response.url
        date_str = self._extract_date(response) or seed_date
        source = self._extract_source(response)
        content = self._extract_content(response)

        summary = seed_snippet or clean_text(content[:120])

        item = self.make_item(  # :contentReference[oaicite:7]{index=7}
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
        if self.validate(item):  # 日期范围用 BaseNewsSpider.within_range → 仅标注，不强拦截无日期；宣传部可后筛
            yield item

    def err_article(self, failure):
        req = getattr(failure, "request", None)
        url = req.url if req else ""
        self.logger.warning("正文抓取失败：%s -> %s", failure.value.__class__.__name__, url)

    # ========== 工具：SearXNG 跳转链接还原 ==========
    def _normalize_searx_href(self, href: str) -> str:
        """
        SearXNG 可能是直接目标链接，也可能走某些中转，做一次规范化；
        目前常见返回已经是直链，这里保持与 nfnews 的“解包跳转”思路一致（以便未来接入更多实例）。 :contentReference[oaicite:8]{index=8}
        """
        if not href:
            return ""
        try:
            p = urlparse(href)
            # 兼容极少数实例会做 /redirect?url= 的方式
            qs = parse_qs(p.query or "")
            for key in ("url", "u", "uddg"):
                if key in qs and qs[key]:
                    real = unquote(qs[key][0])
                    if "%2F" in real or "%3A" in real:
                        real = unquote(real)
                    return real
            return href
        except Exception:
            return href

    # ========== 站内抽取（与 nfnews 结构一致 + 少量 dayoo 适配） ==========
    def _extract_title(self, response: scrapy.http.Response) -> str:
        xp = [
            "//meta[@property='og:title']/@content",
            "//meta[@name='twitter:title']/@content",
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
        if host.endswith("dayoo.com"):
            return "广州日报 / 大洋网"
        return host

    def _extract_content(self, response: scrapy.http.Response) -> str:
        containers = response.xpath(
            "//article | //div[@id='content' or @id='ContentBody' or "
            "contains(@class,'content') or contains(@class,'article') or "
            "contains(@class,'article-content') or contains(@class,'detail') or "
            "contains(@class,'text')]"
        )
        texts = []
        if containers:
            for c in containers:
                p_text = c.xpath(".//p//text()").getall()
                texts.extend(p_text if p_text else c.xpath(".//text()").getall())
        else:
            texts = response.xpath("//body//p//text()").getall() or response.xpath("//body//text()").getall()

        content = clean_text("".join(texts))
        content = re.sub(r"(责任编辑|责编|原标题)[:：].*$", "", content)
        return content.strip()

    # ========== 时间范围推断 ==========
    def _resolve_time_range(self) -> str:
        """
        若用户传了 -a time_range=xxx 就用用户值；
        否则按 start/end 推断离今天的跨度，映射到 SearXNG 的 5 档：
          ""(不限)、day(<=1天)、week(<=7天)、month(<=31天)、year(<=366天)
        仅用于搜索入口；最终筛选仍由 BaseNewsSpider.validate() 负责（以便人工后筛）。 :contentReference[oaicite:9]{index=9}
        """
        if self.time_range_arg in {"", "day", "week", "month", "year"}:
            return self.time_range_arg
        # 未显式给：按 start 距今天的天数做粗分类
        from datetime import datetime as _dt
        if self.start_dt:
            delta = (_dt.now() - self.start_dt).days
            if delta <= 1:
                return "day"
            if delta <= 7:
                return "week"
            if delta <= 31:
                return "month"
            if delta <= 366:
                return "year"
        return ""
