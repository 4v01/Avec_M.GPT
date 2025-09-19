# -*- coding: utf-8 -*-
from __future__ import annotations
import os, re, json, urllib.parse
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional, Tuple
import scrapy
from scrapy import Request
from scrapy.http import Response
from urllib.parse import urlparse, parse_qs
# 依赖工程内父类与工具（ base_search.py 提供）
from .base_search import BaseNewsSpider, parse_date, clean_text  # noqa: E402


def _infer_time_range_from_span(start_dt: Optional[datetime], end_dt: Optional[datetime]) -> str:
    """把 start/end 映射为 SearXNG 接受的 time_range: '', 'day'|'week'|'month'|'year'。"""
    if not (start_dt or end_dt):
        return ""  # 不限
    # 仅用跨度粗判（按起始边界足够了）
    now = datetime.now()
    s = start_dt or (end_dt - timedelta(days=30) if end_dt else now - timedelta(days=30))
    e = end_dt or now
    delta_days = max(1, (e.date() - s.date()).days + 1)
    if delta_days <= 1:
        return "day"
    if delta_days <= 7:
        return "week"
    if delta_days <= 31:
        return "month"
    if delta_days <= 366:
        return "year"
    return ""  # 超出一年，放开


def _parse_result_date_hint(txt: str) -> str:
    """
    从 SearXNG 搜索条目中提取日期提示：
    - 形式如: '11. 4. 2025 — ...' 或 '2025-04-11 ...'
    - 返回原字符串（尽量 yyyy-mm-dd）
    """
    t = txt or ""
    # 1) 11. 4. 2025 / 6. 1. 2025 / 26. 1. 2025
    m = re.search(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b", t)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except Exception:
            pass
    # 2) 2025-04-11 / 2025/04/11 / 2025.04.11
    m = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", t)
    if m:
        y, mth, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


class SearxSearchMixin(BaseNewsSpider):
    """
    通用：用 SearXNG（HTML）做“关键词 + 站点 + 时间范围”检索，然后抓正文。
    子类只需定义：
        - site_host: str              例如 'gov.cn' / 'people.com.cn' / 'xinhuanet.com'
        - name: str                   spider 名
        - allowed_domains: list[str]  包括站点与常见子域（尽量宽）
    运行参数（都“装得像”一点，默认值保守）：
        -a searx_url="https://searx.bndkt.io"   # 可不填；为空时使用内置备选表
        -a time_range=""|"auto"|"day"|"week"|"month"|"year"
        -a max_pages=3
        -a dump_debug=0
    """
    # 默认请求头
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

    # 子类要覆盖
    site_host: str = ""

    def __init__(self,
                 keywords: str = "",
                 start: str = "",
                 end: str = "",
                 searx_url: str = "",
                 time_range: str = "auto",
                 categories: str = "",        # 不强制，默认空
                 max_pages: int = 3,
                 dump_debug: int = 0,
                 **kwargs: Any) -> None:
        super().__init__(keywords=keywords, start=start, end=end, **kwargs)
        self.dump_debug_flag = int(dump_debug or 0)
        self.max_pages = int(max_pages or 1)
        self.categories = (categories or "").strip()  # 留给将来扩展；目前不必传
        # time_range 统一：auto 根据 start/end 推断；传入明确值则尊重
        self.time_range = (time_range or "auto").strip().lower()
        if self.time_range == "auto":
            self.time_range = _infer_time_range_from_span(self.start_dt, self.end_dt)
        # SearX 实例池
        self._instances: Tuple[str, ...] = self._build_instance_pool(searx_url)
        self.logger.info("Searx 实例池：%s", ",".join(self._instances))

    # --------- 入口 ---------
    def start_requests(self) -> Iterable[Request]:
        if not self.keywords:
            self.logger.warning("未提供关键词，直接结束。")
            return
        query = f"{self.keywords} site:{self.site_host}".strip()
        # 只用 HTML；不使用 RSS（该实例 RSS 不稳定且字段贫乏）
        params = {
            "q": query,
            "language": "all",
            "safesearch": "0",
            "pageno": "1",
        }
        if self.time_range in {"day", "week", "month", "year"}:
            params["time_range"] = self.time_range
        # categories 可选；不要强制过滤
        if self.categories:
            params["categories"] = self.categories

        url = self._join(self._instances[0], "/search", params)
        meta = {"page": 1, "q": query, "instance_idx": 0}
        self.logger.info("HTML GET：%s", url)
        yield Request(url, callback=self.parse_html, meta=meta, dont_filter=True)

    # --------- 列表解析 ----------
    def parse_html(self, response: Response):
        if self.dump_debug_flag:
            self.dump_debug(response, f"list_p{response.meta.get('page',1)}")
        sel = response
        items = sel.xpath("//article[contains(@class,'result') and .//a[@href]]")
        total = len(items)

        # 调试记录（作为普通 item 交给下游统计）
        yield {
            "type": "pagination_info",
            "page": int(response.meta.get("page", 1)),
            "query": response.meta.get("q", ""),
            "results_count": total,
            "site": getattr(self, "site_host", self.name),
            "backend": "searx_html",
            "searx_time_range": self.time_range or "",
            "url": response.url,
        }

        # 逐条派发正文请求
        rank = 0
        for node in items:
            rank += 1
            a = node.xpath(".//h3/a | .//a[contains(@class,'url_header') or contains(@class,'result__url')]")[0]
            href = (a.xpath("./@href").get() or "").strip()
            title = clean_text("".join(a.xpath(".//text()").getall()))

            # 只保留目标域
            if not self._is_target_domain(href):
                continue

            # 摘要 & 日期提示
            snippet = clean_text("".join(node.xpath(".//p[contains(@class,'content')]//text()").getall()))
            # 有些引擎把日期拼在摘要前段
            date_hint = _parse_result_date_hint(snippet)

            meta = {
                "seed_title": title,
                "seed_snippet": snippet,
                "seed_date": date_hint,
                "seed_rank": rank,
            }
            yield Request(href, callback=self.parse_article, errback=self.err_article, meta=meta, dont_filter=True)

        # 翻页
        page = int(response.meta.get("page", 1))
        if page < self.max_pages:
            # SearXNG 的下一页：POST 或 GET 均可能，这里用“下一页”按钮的表单或直接构造 pageno
            next_btn = sel.xpath("//nav[@id='pagination']//form[contains(@class,'next_page')]//input[@name='pageno']/@value").get()
            if next_btn:
                next_pageno = str(int(next_btn) + 0)  # 页面里已给下一页页码
                params = self._extract_search_params_from_url(response.url)
                params["pageno"] = next_pageno
                next_url = self._join(self._instances[0], "/search", params)
                meta2 = {"page": page + 1, "q": response.meta.get("q",""), "instance_idx": response.meta.get("instance_idx",0)}
                yield Request(next_url, callback=self.parse_html, meta=meta2, dont_filter=True)

    # --------- 正文解析 ----------
    def parse_article(self, response: Response):
        if self.dump_debug_flag:
            host = urlparse(response.url).hostname or "page"
            self.dump_debug(response, f"detail_{host}")

        seed_title = response.meta.get("seed_title") or ""
        seed_snippet = response.meta.get("seed_snippet") or ""
        seed_date = response.meta.get("seed_date") or ""

        ctype = (response.headers.get(b"Content-Type") or b"").decode("utf-8","ignore").lower()

        # 非 HTML：如 PDF / DOC / XLS / JSON 等，直接构造条目（不要 xpath）
        if ("text/html" not in ctype) and ("xml" not in ctype):
            fallback_title = seed_title or os.path.basename(urlparse(response.url).path) or response.url
            item = self.make_item(
                title=fallback_title,
                url=response.url,
                summary=seed_snippet,
                source=self._source_from_host(response.url),
                date=seed_date,
                site=getattr(self, "site_host", self.name),
                extra={"content": "", "content_type": ctype, "binary": True}
            )
            if self.validate(item):
                yield item
            return

        # HTML：抽取
        title = self._extract_title(response) or seed_title or response.url
        date_str = self._extract_date(response) or seed_date
        source = self._extract_source(response) or self._source_from_host(response.url)
        content = self._extract_content(response)
        summary = seed_snippet or clean_text(content[:160])

        item = self.make_item(
            title=title,
            url=response.url,
            summary=summary,
            source=source,
            date=clean_text(date_str),
            site=getattr(self, "site_host", self.name),
            extra={"content": content, "domain": urlparse(response.url).hostname or ""}
        )
        if self.validate(item):
            yield item

    # --------- 错误处理 ----------
    def err_article(self, failure):
        req = getattr(failure, "request", None)
        url = req.url if req else ""
        self.logger.warning("正文抓取失败：%s -> %s", failure.value.__class__.__name__, url)

    # --------- 工具 ----------
    def _is_target_domain(self, href: str) -> bool:
        if not href:
            return False
        try:
            host = (urlparse(href).hostname or "").lower()
            site = self.site_host.lower()
            return host == site or host.endswith("." + site)
        except Exception:
            return False

    def _source_from_host(self, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        return host

    def _extract_title(self, response: Response) -> str:
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
                    v = re.sub(r"\s*[-_｜|·].*$", "", v)
                if v:
                    return v
        return ""

    def _extract_date(self, response: Response) -> str:
        meta_x = [
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='pubdate']/@content",
            "//meta[@name='publishdate']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@name='date']/@content",
        ]
        for p in meta_x:
            v = response.xpath(p).get()
            if v:
                return clean_text(v)
        # 常见中文正文时间
        plain_x = [
            "//*[contains(@class,'time')]/text()",
            "//*[contains(@class,'date')]/text()",
            "//*[contains(text(),'发布时间')]/following::text()[1]",
            "//*[contains(text(),'发表时间')]/following::text()[1]",
            "//*[contains(text(),'时间：')]/following::text()[1]",
        ]
        for p in plain_x:
            v = clean_text("".join(response.xpath(p).getall()))
            m = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}(?:\s+\d{2}:\d{2})?)", v)
            if m:
                return m.group(1)
        # 最后兜底：全文扫
        m = re.search(r"(\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}(?:[T\s]\d{2}:\d{2}:\d{2})?)", response.text or "")
        return m.group(1) if m else ""

    def _extract_source(self, response: Response) -> str:
        v = response.xpath("//meta[@property='og:site_name']/@content").get()
        if v:
            return clean_text(v)
        txt = clean_text(" ".join(response.xpath("//*[contains(text(),'来源')]/text()").getall()))
        m = re.search(r"来源[:：]\s*([^\s|｜]+)", txt)
        if m:
            return clean_text(m.group(1))
        return ""

    def _extract_content(self, response: Response) -> str:
        containers = response.xpath(
            "//article | //div[@id='content' or @id='ContentBody' or "
            "contains(@class,'content') or contains(@class,'article') or "
            "contains(@class,'article-content') or contains(@class,'detail') or "
            "contains(@class,'text') or contains(@class,'TRS_Editor')]"
        )
        texts: Iterable[str] = []
        if containers:
            buf = []
            for c in containers:
                p_text = c.xpath(".//p//text()").getall()
                buf.extend(p_text if p_text else c.xpath(".//text()").getall())
            texts = buf
        else:
            texts = response.xpath("//body//p//text()").getall() or response.xpath("//body//text()").getall()
        content = clean_text("".join(texts))
        content = re.sub(r"(责任编辑|责编|原标题|校对)[:：].*$", "", content)
        return content.strip()

    def _extract_search_params_from_url(self, url: str) -> Dict[str, str]:
        try:
            q = urllib.parse.urlparse(url).query
            d = {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}
            # 只保留我们关心的
            keep = {"q","language","safesearch","time_range","pageno","categories"}
            return {k: d[k] for k in d if k in keep}
        except Exception:
            return {}

    def _join(self, base: str, path: str, params: Dict[str, str]) -> str:
        base2 = base.rstrip("/")
        path2 = path if path.startswith("/") else "/" + path
        if params:
            return f"{base2}{path2}?{urllib.parse.urlencode(params, doseq=False, safe='')}"
        return f"{base2}{path2}"

    def _build_instance_pool(self, first: str) -> Tuple[str, ...]:
        # 允许通过 -a searx_url 指定；为空走内置列表
        cands = []
        if first:
            cands.append(first.strip().rstrip("/"))
        # 内置备份（可按需增减）
        cands.extend([
            "https://searx.bndkt.io",
            "https://searx.fmac.xyz",
            "https://searx.thegpm.org",
        ])
        # 去重
        seen, out = set(), []
        for u in cands:
            if u and (u not in seen):
                out.append(u); seen.add(u)
        return tuple(out or ("https://searx.bndkt.io",))


# ================== 多个站点 ==================

class GovCnSearchSpider(SearxSearchMixin):
    name = "govcn_search"
    site_host = "gov.cn"
    allowed_domains = ["gov.cn"]  # Scrapy 只做浅校验；子域没关系
    # gov 站经常有附件（pdf/doc/xls），本 Mixin 已处理非 HTML


class PeopleSearchSpider(SearxSearchMixin):
    name = "people_search"
    site_host = "people.com.cn"
    allowed_domains = ["people.com.cn", "rmrb.com.cn", "paper.people.com.cn"]


class XinhuanetSearchSpider(SearxSearchMixin):
    name = "xinhuanet_search"
    site_host = "xinhuanet.com"
    allowed_domains = ["xinhuanet.com", "news.cn", "xhpfm.com"]

class GDTVSearchSpider(SearxSearchMixin):
    """广东广播电视台（荔枝网）
    该站点标题经常由 JS 渲染，但 <title> 在首屏可用；
    另外时间常见于 meta/article JSON 或页面“发布时间”。
    """
    name = "GDTV_search"
    site_host = "gdtv.cn"
    allowed_domains = ["gdtv.cn", "www.gdtv.cn"]

    # ——仅针对 gdtv 定制更强的抽取策略——
    def _extract_title(self, response: Response) -> str:  # type: ignore[override]
        # 1) 直接用 <title>，去掉品牌后缀
        t = response.xpath("//title/text()").get()
        if t:
            t = clean_text(t)
            # 常见形式："xxx - 要闻 - 广东台荔枝网"
            t = re.split(r"\s*[-_｜|·]\s*", t)[0]
            if t:
                return t
        # 2) 再试 og:title / twitter:title
        for xp in ("//meta[@property='og:title']/@content",
                   "//meta[@name='twitter:title']/@content"):
            v = response.xpath(xp).get()
            if v:
                v = clean_text(v)
                if v:
                    return v
        # 3) 最后兜底父类
        return super()._extract_title(response)

    def _extract_date(self, response: Response) -> str:  # type: ignore[override]
        # 先查常见 meta
        for xp in (
            "//meta[@property='article:published_time']/@content",
            "//meta[@name='publishdate']/@content",
            "//meta[@name='pubdate']/@content",
            "//meta[@itemprop='datePublished']/@content",
            "//meta[@name='date']/@content",
        ):
            v = response.xpath(xp).get()
            if v:
                return clean_text(v)
        # JSON-LD 里常有 datePublished
        ld = "".join(response.xpath("//script[@type='application/ld+json']/text()").getall())
        if ld:
            try:
                data = json.loads(ld)
                if isinstance(data, dict):
                    for k in ("datePublished", "uploadDate", "pubdate", "publishDate", "dateCreated"):
                        if data.get(k):
                            return clean_text(str(data[k]))
                elif isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict):
                            for k in ("datePublished", "uploadDate", "pubdate", "publishDate", "dateCreated"):
                                if d.get(k):
                                    return clean_text(str(d[k]))
            except Exception:
                pass
        # 页面可见时间
        for xp in (
            "//*[contains(@class,'time')]/text()",
            "//*[contains(@class,'date')]/text()",
            "//*[contains(text(),'发布时间')]/following::text()[1]",
            "//*[contains(text(),'发表时间')]/following::text()[1]",
            "//*[contains(text(),'时间：')]/following::text()[1]",
        ):
            v = clean_text("".join(response.xpath(xp).getall()))
            m = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{2}:\d{2})?)", v)
            if m:
                return m.group(1)
        # 父类兜底（全文扫描）
        return super()._extract_date(response)


class CNRSearchSpider(SearxSearchMixin):
    name = "CNR_search"
    site_host = "cnr.cn"
    allowed_domains = ["cnr.cn"]


class ChianaNewsSearchSpider(SearxSearchMixin):
    name = "Chinanews_search"
    site_host = "chinanews.com"
    allowed_domains = ["chinanews.com"]


class XuexiSearchSpider(SearxSearchMixin):
    name = "xuexi_search"
    site_host = "xuexi.cn"
    allowed_domains = ["xuexi.cn"]
