# -*- coding: utf-8 -*-
import re
import json
import html as html_module
import urllib.parse
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response
from scrapy_playwright.page import PageMethod

from .base_search import BaseNewsSpider, parse_date, fmt_date


class IFengSearchSpider(BaseNewsSpider):
    """
    凤凰网搜索（结果页收集 → 详情页穿透 → 可选二跳原站，提取原发源 & 发布时间）
    用法：
      scrapy crawl ifeng_search -a keywords="罗冲围" -a start=2025-03-01 -a end=2025-09-01 -O ifeng.jl
    """
    name = "ifeng_search"

    # 先限定 ifeng 站群；二跳原站时会动态追加 allowed_domains
    allowed_domains = [
        "so.ifeng.com",
        "ifeng.com", "news.ifeng.com", "finance.ifeng.com",
        "ent.ifeng.com", "tech.ifeng.com", "i.ifeng.com", "ishare.ifeng.com",
    ]

    search_base = "https://so.ifeng.com/?q={q}"

    custom_settings = {
        **(BaseNewsSpider.custom_settings or {}),
        "ROBOTSTXT_OBEY": False,
        "DOWNLOAD_DELAY": 0.2,
        "CONCURRENT_REQUESTS": 8,
        "AUTOTHROTTLE_ENABLED": True,
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 45000,
    }

    # —— 结果列表入口（标题/封面）选择器 ——（保守列举）
    ENTRY_SELECTORS = (
        "a.news-stream-newsStream-image-link[href], "
        "a.news-stream-newsStream-title[href], "
        "a.news-stream-newsStream-title-link[href], "
        "h3 a[href], .news-title a[href], .title a[href]"
    )

    # 列表页容器内字段
    SUMMARY_SELECTORS = (
        ".news-stream-newsStream-abstract, .news-stream-newsStream-des, "
        ".summary, .desc, .c-abstract, p"
    )
    DATE_SELECTORS = (
        ".news-stream-newsStream-time, .news-time, .time, .date, .news-meta, .news-from, .source"
    )

    # 详情页：来源/时间的半随机类名（CSS Modules 风格）——用属性包含匹配
    DETAIL_SOURCE_SELECTORS = (
        'span[class*="index_source"] ::text, span[class*="wemedia"] ::text, '
        '.source ::text, .news-from ::text'
    )
    DETAIL_TIME_SELECTORS = (
        'span[class*="index_time"]::text, span[class*="time"]::text, '
        'p[class*="time"]::text, .news-time::text, .time::text, .date::text'
    )

    # 文本兜底
    ORIGIN_RE = re.compile(r"(?:来源|来源于|稿源)\s*[:：]\s*([^\s·|丨｜\u3000]+)")
    PUBLISHED_RE = re.compile(
        r"(?:发布时间|发表时间|发表日期|发布于|时间)\s*[:：]?\s*([0-9]{4}[^\n，。]*)"
    )

    @staticmethod
    def _clean_title(s: str) -> str:
        if not s:
            return ""
        s = html_module.unescape(s)
        s = re.sub(r"</?em>", "", s, flags=re.I)  # 去掉高亮 <em>
        return s.strip()

    async def start(self):
        if not self.keywords:
            self.logger.warning("缺少 -a keywords=... 参数")
            return
        q = urllib.parse.quote(self.keywords.strip())
        url = self.search_base.format(q=q)
        yield scrapy.Request(
            url,
            callback=self.parse_results,
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "networkidle"),
                    PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"),
                    PageMethod("wait_for_timeout", 800),
                ],
                "q": q,
            },
            dont_filter=True,
        )

    def parse_results(self, response: Response):
        anchors = response.css(self.ENTRY_SELECTORS)
        if not anchors:
            path = self.dump_debug(response, tag="no_entry")
            self.logger.info("IFENG: 未命中入口选择器，已落盘：%s", path)
            return

        seen = set()
        for a in anchors:
            href = (a.attrib.get("href") or "").strip()
            if not href:
                continue
            share_url = response.urljoin(href)
            if "ifeng.com" not in share_url:
                continue
            if share_url in seen:
                continue
            seen.add(share_url)

            # 列表页信息（先收，详情页再补充/修正）
            title = a.attrib.get("title", "") or (a.css("img[alt]::attr(alt)").get() or "")
            if not title:
                title = self._clean_title(self.text_of(a))
            title = self._clean_title(title) or share_url

            container = None
            for xp in [
                "ancestor::li[1]",
                "ancestor::div[contains(@class,'news-stream')][1]",
                "ancestor::div[contains(@class,'result')][1]",
                "ancestor::div[contains(@class,'box')][1]",
            ]:
                cand = a.xpath(xp)
                if cand:
                    container = cand[0]
                    break
            summary = self.text_of(container.css(self.SUMMARY_SELECTORS)) if container is not None else ""
            list_date_text = self.text_of(container.css(self.DATE_SELECTORS)) if container is not None else ""
            list_ds = fmt_date(parse_date(list_date_text)) or (list_date_text.strip() if list_date_text else "")

            yield scrapy.Request(
                share_url,
                callback=self.parse_detail,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "domcontentloaded"),
                        PageMethod("wait_for_timeout", 500),
                    ],
                    "seed_item": {
                        "title": title,
                        "summary": summary,
                        "list_date": list_ds,
                        "share_url": share_url,
                    },
                },
                dont_filter=True,
            )

    # -------------------- 详情页穿透（ifeng） --------------------
    def parse_detail(self, response: Response):
        seed = response.meta.get("seed_item") or {}
        share_url = seed.get("share_url") or response.url

        # 1) canonical / og:url（有些 ifeng 自己的 canonical 仍指向 ishare，别急着当 origin）
        canonical = response.css("link[rel=canonical]::attr(href)").get() or ""
        og_url = response.css("meta[property='og:url']::attr(content)").get() or ""
        # 优先正文中外链原文，其次 og/canonical
        origin_url_candidates = []

        # 2) 详情页内直接可见的“来源/时间”（适配 index_source_ / index_time_ 这类随机类名）
        origin_name = ""
        for t in response.css(self.DETAIL_SOURCE_SELECTORS).getall():
            tt = (t or "").strip()
            if tt and tt != "凤凰网":
                origin_name = tt
                break
        # 如果没抓到，再文本兜底
        if not origin_name:
            vis_text = " ".join(response.css("body *::text").getall())[:4000]
            m_origin = self.ORIGIN_RE.search(vis_text or "")
            if m_origin:
                origin_name = m_origin.group(1).strip()

        # 3) 详情页发布时间：先结构化，再文本兜底，再回退到列表页时间
        ld_date = ""
        ld_publisher = ""
        for node in response.css("script[type='application/ld+json']::text").getall():
            node = node.strip()
            if not node:
                continue
            try:
                data = json.loads(node)
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for obj in items:
                if isinstance(obj, dict):
                    if not ld_publisher and isinstance(obj.get("publisher"), dict):
                        ld_publisher = obj["publisher"].get("name") or ""
                    if not ld_date:
                        ld_date = (obj.get("datePublished") or obj.get("dateCreated") or "") or ld_date

        meta_date = (
            response.css("meta[property='article:published_time']::attr(content)").get()
            or response.css("meta[name='pubdate']::attr(content)").get()
            or response.css("meta[name='publishdate']::attr(content)").get()
            or response.css("time[datetime]::attr(datetime)").get()
            or ""
        )

        # index_time_ / time 文本
        time_text = ""
        for t in response.css(self.DETAIL_TIME_SELECTORS).getall():
            tt = (t or "").strip()
            if tt:
                time_text = tt
                break

        # 文本兜底“发布时间：”
        if not (ld_date or meta_date or time_text):
            vis_text = " ".join(response.css("body *::text").getall())[:4000]
            m_pub = self.PUBLISHED_RE.search(vis_text or "")
            time_text = (m_pub.group(1).strip() if m_pub else "")

        pub_raw = ld_date or meta_date or time_text or seed.get("list_date", "")
        pub_ds = fmt_date(parse_date(pub_raw)) or seed.get("list_date", "")

        # 4) 找原站链接（正文区域 a[href^=http] 且域名非 ifeng）
        for sel in ["article a[href^='http']", ".article a[href^='http']", "#article a[href^='http']"]:
            for a in response.css(sel):
                href = (a.attrib.get("href") or "").strip()
                if not href:
                    continue
                absu = response.urljoin(href)
                if "ifeng.com" in absu:
                    continue
                # 优先包含“原文/来源”或包含来源名的链接文本
                at = (self.text_of(a) or "").strip()
                if ("原文" in at) or ("来源" in at) or (origin_name and (origin_name in at)):
                    origin_url_candidates.append(absu)
                else:
                    # 次优：也先记下，后面没优先项再用
                    origin_url_candidates.append(absu)

        # 再考虑 og/canonical（若它们不是 ifeng 域）
        for u in [og_url, canonical]:
            if u and ("ifeng.com" not in u):
                origin_url_candidates.append(response.urljoin(u))

        # 去重
        seen = set()
        origin_url_candidates = [u for u in origin_url_candidates if not (u in seen or seen.add(u))]

        origin_url = origin_url_candidates[0] if origin_url_candidates else ""

        # 5) 若发现 origin_url，则二跳抓原站；需要让 OffsiteMiddleware 放行：动态追加域名
        if origin_url:
            od = urlparse(origin_url).netloc
            if od and od not in self.allowed_domains:
                self.allowed_domains.append(od)

            # 详情页先做一份“过渡 item”，等原站回来再定稿
            base_payload = {
                "title": seed.get("title") or self._clean_title(self.text_of(response.css("h1, .article-title, .title"))) or response.url,
                "summary": seed.get("summary") or self.text_of(
                    response.css("meta[name='description']::attr(content), article p, .article p, #article p, .text p")
                )[:300],
                "share_url": share_url,
                "origin_name_hint": origin_name or ld_publisher,  # 原站名线索
                "ifeng_pub_ds": pub_ds,                           # 若原站失败，至少保留 ifeng 的时间
            }

            yield scrapy.Request(
                origin_url,
                callback=self.parse_origin,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "domcontentloaded"),
                        PageMethod("wait_for_timeout", 600),
                    ],
                    "origin_url": origin_url,
                    "payload": base_payload,
                },
                dont_filter=True,
            )
            return  # 等二跳回来定稿

        # 6) 没有原站链接 → 直接以 ifeng 信息定稿（但 source 用我们识别到的来源名）
        final_title = seed.get("title") or self._clean_title(self.text_of(response.css("h1, .article-title, .title"))) or response.url
        final_summary = seed.get("summary") or self.text_of(
            response.css("meta[name='description']::attr(content), article p, .article p, #article p, .text p")
        )[:300]
        final_source = origin_name or ld_publisher or response.css("meta[name='source']::attr(content)").get() \
            or response.css("meta[property='og:site_name']::attr(content)").get() or "凤凰网"

        item = self.make_item(
            title=final_title,
            url=share_url,
            summary=final_summary,
            source=final_source,
            date=pub_ds,
            site="ifeng_search",
            extra={
                "origin": final_source,
                "origin_url": "",            # 无原站链接
                "share_url": share_url,
                "publisher_domain": urlparse(share_url).netloc,
            },
        )
        if self.validate(item):
            yield item

    # -------------------- 二跳：原站落地页 --------------------
    def parse_origin(self, response: Response):
        origin_url = response.meta.get("origin_url") or response.url
        payload = response.meta.get("payload") or {}

        # 1) 原站结构化发布时间 & 发布主体
        ld_date = ""
        ld_publisher = ""
        for node in response.css("script[type='application/ld+json']::text").getall():
            node = node.strip()
            if not node:
                continue
            try:
                data = json.loads(node)
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for obj in items:
                if isinstance(obj, dict):
                    if not ld_publisher and isinstance(obj.get("publisher"), dict):
                        ld_publisher = obj["publisher"].get("name") or ""
                    if not ld_date:
                        ld_date = (obj.get("datePublished") or obj.get("dateCreated") or "") or ld_date

        meta_date = (
            response.css("meta[property='article:published_time']::attr(content)").get()
            or response.css("meta[name='publishdate']::attr(content)").get()
            or response.css("meta[name='pubdate']::attr(content)").get()
            or response.css("meta[name='ptime']::attr(content)").get()
            or response.css("time[datetime]::attr(datetime)").get()
            or ""
        )

        # 可见文本兜底
        vis_text = " ".join(response.css("body *::text").getall())[:6000]
        m_pub = self.PUBLISHED_RE.search(vis_text or "")
        text_date = (m_pub.group(1).strip() if m_pub else "")

        pub_raw = ld_date or meta_date or text_date or payload.get("ifeng_pub_ds", "")
        pub_ds = fmt_date(parse_date(pub_raw)) or payload.get("ifeng_pub_ds", "")

        # 2) 原站的发布主体（来源名）——多路
        origin_name = (
            ld_publisher
            or response.css("meta[property='og:site_name']::attr(content)").get()
            or response.css("meta[name='source']::attr(content)").get()
            or payload.get("origin_name_hint")
            or urlparse(origin_url).netloc
        )

        # 3) 标题/摘要兜底：以原站为准（没有就沿用 ifeng 的）
        title = self._clean_title(
            self.text_of(response.css("h1, .title, .article-title")) or payload.get("title")
        ) or origin_url
        summary = payload.get("summary") or self.text_of(
            response.css("meta[name='description']::attr(content), article p, .article p, #article p")
        )[:300]

        item = self.make_item(
            title=title,
            url=origin_url,               # 用原站链接作为最终 URL
            summary=summary,
            source=origin_name,           # 用原站名作为最终 source
            date=pub_ds,                  # 用原站发布时间
            site="ifeng_search",
            extra={
                "origin": origin_name,
                "origin_url": origin_url,
                "share_url": payload.get("share_url"),
                "publisher_domain": urlparse(origin_url).netloc,
                "origin_crawled": True,
            },
        )
        if self.validate(item):
            yield item
