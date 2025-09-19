# -*- coding: utf-8 -*-
import os
import re
from urllib.parse import urlparse, parse_qs, unquote

import scrapy
from scrapy import Request
from scrapy_playwright.page import PageMethod

from .base_search import BaseNewsSpider, clean_text as base_clean_text  # fmt_date 未实际使用，可去掉


def ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path

# === 更新点：统一字符串清洗：去空白 + 去 <em> 包装 ===
_EM_RE = re.compile(r"</?em[^>]*>", re.I)

def clean_text(s: str) -> str:
    if not s:
        return ""
    # 先去 <em>，再用基类的 clean_text 做空白规整
    s = _EM_RE.sub("", s)
    return base_clean_text(s)

def extract_redirect(url: str) -> str:
    """解析 chinaso 的 /link?url= 真实跳转"""
    try:
        if not url:
            return ""
        u = urlparse(url)
        if u.netloc.endswith("chinaso.com") and u.path.startswith("/link"):
            qs = parse_qs(u.query or "")
            enc = qs.get("url", [""])[0]
            return unquote(enc) if enc else ""
    except Exception:
        pass
    return ""


class ChinaSoSearchSpider(BaseNewsSpider):
    name = "chinaso_search"
    allowed_domains = ["chinaso.com", "m.chinaso.com", "www.chinaso.com"]

    custom_settings = {
        "CONCURRENT_REQUESTS": 3,
        "DOWNLOAD_DELAY": 0.5,
        "RETRY_TIMES": 3,
        "AUTOTHROTTLE_ENABLED": True,
        # Playwright
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        "DOWNLOAD_HANDLERS": {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 25_000,
        "PLAYWRIGHT_ABORT_REQUEST": None,
    }

    SOURCE_BLACK_KEYWORDS = ["售楼", "房价", "新房", "营销"]
    TITLE_SOFT_BLOCK_RE = re.compile(r"(官方售楼处|线上售楼处|开发商售楼处|营销中心|热搜好房|AI热搜|推广|广告)", re.I)

    # === 更新点：继承 BaseNewsSpider 的初始化签名；仅增加 max_pages 参数 ===
    def __init__(self, keywords: str = "", max_pages: int = 1, **kwargs):
        super().__init__(keywords=keywords, start=kwargs.pop("start", ""), end=kwargs.pop("end", ""), **kwargs)
        try:
            self.max_pages = int(max_pages)
        except Exception:
            self.max_pages = 1

        # 调试目录
        self.debug_dir = os.path.join("var", "debug")
        os.makedirs(self.debug_dir, exist_ok=True)
        self.logger.info("chinaso_search init: kw='%s', pages=%s", self.keywords, self.max_pages)

    # 2.13 兼容：不遮蔽框架的 async start
    async def start(self):
        async for req in super().start():
            yield req

    # 入口走移动端，失败再兜底桌面端
    def start_requests(self):
        # === 更新点：手机端入口 URL 与历史一致，pn=0 ===
        m_url = f"https://m.chinaso.com/newssearch/news/results?q={self.keywords}&tn=news&pn=0"
        yield Request(
            m_url,
            meta={
                "playwright": True,
                "playwright_include_page": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_selector", "div.list-item, div[class*='list'] div[class*='item']", timeout=10_000)
                ],
                "page_no": 1,
                "mode": "mobile",
            },
            callback=self.parse_mobile,
        )

    # ---------------------- Mobile parse --------------------- #
    def parse_mobile(self, response: scrapy.http.Response):
        page_no = response.meta.get("page_no", 1)

        sel = scrapy.Selector(text=response.text)
        # 多种兜底的容器：你给的结构（type-news -> list-item）也涵盖在内
        candidates = sel.css(
            "div.list-item, div.type-news .list-item, div[class*='list'] div[class*='item']"
        )
        self.logger.info("[ChinaSo] 移动端候选卡片: %s 条", len(candidates))

        produced = 0
        for idx, node in enumerate(candidates):
            # 优先：直接找 <a href>
            a = node.css("a[href]")
            link = ""
            if a:
                link = (a.attrib.get("href") or "").strip()

            # === 更新点：无 <a> 时，按你给的 DOM 抽取字段；并兜底尝试找链接 ===
            # 标题：.item-title .title-text（含 <em>），统一走 string(.) + 去 <em>
            title = clean_text(node.css(".item-title .title-text").xpath("string(.)").get())
            if not title and a:
                # 次选：a 文本（也会包含 <em>，clean_text 已处理）
                title = clean_text(a.xpath("string(.)").get())

            # 摘要：.item-content .desc-text 或其它 summary/p
            summary = clean_text(
                node.css(".item-content .desc-text, .content-desc .desc-text, .summary, p").xpath("string(.)").get()
            )
            # 来源/时间
            source = clean_text(node.css(".info-source::text, .source-name::text").get())
            rel_time = clean_text(node.css(".info-time::text, .source-time::text").get())

            # 兜底找链接：data-url / onclick 里的 http(s)
            if not link:
                data_url = (node.attrib.get("data-url") or "").strip()
                if data_url:
                    link = data_url
            if not link:
                onclicks = node.xpath(".//@onclick").getall() or []
                for oc in onclicks:
                    m = re.search(r"(https?://[^\s'\"<>]+)", oc)
                    if m:
                        link = m.group(1)
                        break

            # 还原 chinaso 跳转
            real = extract_redirect(link) or link

            # 过滤规则保持不变（只作用于变量内容）
            if not title:
                continue
            if self.is_soft_block_title(title):
                continue
            if self.is_filtered_by_source(source):
                continue

            # 产出（用基类工厂，保持 pipelines 兼容）
            item = self.make_item(
                title=title,
                url=real,
                summary=summary,
                source=source,
                date=rel_time,   # 相对时间字符串，保留原样；下游如需可再标准化
                site="chinaso",
                extra={"mode": "mobile", "page_no": page_no, "pos": idx},
            )
            # 日期范围（如果你传了 -a start/end）
            if not self.within_range(self.parse_date_from_str(rel_time)):
                continue

            produced += 1
            yield item

        # 移动端未产出 -> 桌面端兜底
        if produced == 0:
            self.logger.info("[ChinaSo] 移动端未产出，尝试桌面端…")
            desk_url = f"https://www.chinaso.com/newssearch/all/allResults?q={self.keywords}&tn=news&pn=1"
            yield Request(
                desk_url,
                meta={
                    "playwright": True,
                    "playwright_include_page": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_selector", "#compnt a.common-title, .search-list a.common-title", timeout=10_000)
                    ],
                    "page_no": 1,
                    "mode": "desktop",
                },
                callback=self.parse_desktop,
            )

    # --------------------- Desktop parse --------------------- #
    def parse_desktop(self, response: scrapy.http.Response):
        page_no = response.meta.get("page_no", 1)
        sel = scrapy.Selector(text=response.text)

        blocks = sel.css("#compnt .list")
        anchors = sel.css("#compnt .list .container a.common-title, .search-list a.common-title")

        self.logger.info("[ChinaSo][desktop] 第 %s 页：块 %s / a 标签 %s", page_no, len(blocks), len(anchors))

        if not anchors:
            # 落整页帮助调试
            fname = ensure_dir(os.path.join(self.debug_dir, f"chinaso_search_desktop_p{page_no}_no_results_{hash(response.text) & 0xffffffff:08x}.html"))
            with open(fname, "w", encoding="utf-8") as f:
                f.write(response.text)
            self.logger.warning("[ChinaSo][desktop] 第 %s 页暂无可解析结果 -> %s", page_no, fname)
            return

        produced = 0
        for idx, card in enumerate(sel.css("#compnt .list")):
            a = card.css(".container a.common-title")
            if not a:
                continue
            link = (a.attrib.get("href") or "").strip()
            title = clean_text(a.xpath("string(.)").get())
            summary = clean_text(card.css(".container .common-summary").xpath("string(.)").get())
            source = clean_text(card.css(".source .source-name::text").get())
            rel_time = clean_text(card.css(".source .source-time::text").get())

            if not title or not link:
                continue

            real = extract_redirect(link) or link

            if self.is_soft_block_title(title) or self.is_filtered_by_source(source):
                continue

            item = self.make_item(
                title=title,
                url=real,
                summary=summary,
                source=source,
                date=rel_time,
                site="chinaso",
                extra={"mode": "desktop", "page_no": page_no, "pos": idx},
            )
            if not self.within_range(self.parse_date_from_str(rel_time)):
                continue

            produced += 1
            yield item

        # （可选）翻页逻辑保留原样，这里不额外扩展

    # --- 规则/工具：保持原有策略，仅以方法形态组织 ---
    def is_filtered_by_source(self, source: str) -> bool:
        s = source or ""
        for kw in self.SOURCE_BLACK_KEYWORDS:
            if kw in s:
                return True
        return False

    def is_soft_block_title(self, title: str) -> bool:
        return bool(self.TITLE_SOFT_BLOCK_RE.search(title or ""))

    # 解析相对时间：尽量宽松；用于 within_range 判断（不强制标准化输出）
    def parse_date_from_str(self, s: str):
        s = (s or "").strip()
        if not s:
            return None
        # “8天前 / 8小时前 / 30分钟前”
        m = re.search(r"(\d+)\s*分钟前", s)
        if m:
            from datetime import datetime, timedelta
            return datetime.now() - timedelta(minutes=int(m.group(1)))
        m = re.search(r"(\d+)\s*小时前", s)
        if m:
            from datetime import datetime, timedelta
            return datetime.now() - timedelta(hours=int(m.group(1)))
        m = re.search(r"(\d+)\s*天前", s)
        if m:
            from datetime import datetime, timedelta
            return datetime.now() - timedelta(days=int(m.group(1)))
        # 兜底：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD
        m = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", s)
        if m:
            from datetime import datetime
            raw = m.group(1).replace("/", "-").replace(".", "-")
            try:
                return datetime.strptime(raw, "%Y-%m-%d")
            except Exception:
                return None
        return None
