# pr_crawler/spiders/ycwb_search.py
# -*- coding: utf-8 -*-
import urllib.parse
import scrapy
from scrapy.http import Response
from scrapy_playwright.page import PageMethod
from .base_search import BaseNewsSpider, parse_date, fmt_date

class YCWBSearchSpider(BaseNewsSpider):
    """
    金羊网（羊城晚报电子网）搜索直达
      用法：
        scrapy crawl ycwb_search -a keywords="聚龙湾" -a start=2025-03-01 -a end=2025-09-01 -O ycwb.jl
    """
    name = "ycwb_search"
    allowed_domains = ["se.ycwb.com", "ycwb.com", "news.ycwb.com", "wap.ycwb.com"]
    search_base = "https://se.ycwb.com/?q={q}"  # ✅ 没有翻页参数

    custom_settings = {
        **(BaseNewsSpider.custom_settings or {}),
        "ROBOTSTXT_OBEY": False,  # ✅ 忽略 robots
        "DOWNLOAD_DELAY": 0.2,
        "CONCURRENT_REQUESTS": 8,
        "AUTOTHROTTLE_ENABLED": True,
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 30000,
    }

    # Scrapy 2.13 起推荐 start() 而不是 start_requests()
    async def start(self):
        if not self.keywords:
            self.logger.warning("缺少 -a keywords=... 参数")
            return
        q = urllib.parse.quote(self.keywords.strip())
        yield scrapy.Request(
            self.search_base.format(q=q),
            callback=self.parse_results,
            meta={
                "playwright": True,
                # 等网络空闲 + 结果列表渲染完成；顺手滚动一下防懒加载
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "networkidle"),
                    PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"),
                    PageMethod("wait_for_selector", "#relist .re-item, .re-list .re-item", timeout=15000),
                ],
                "q": q,
            },
            dont_filter=True,
        )

    def parse_results(self, response: Response):
        # 结果块：兼容两种容器 id/class
        cards = response.css("#relist .re-item, .re-list .re-item")
        scraped = 0

        for it in cards:
            a = it.css("h3 a[href]")
            href = (a.attrib.get("href") or "").strip()
            if not href:
                continue
            url = response.urljoin(href)
            # 只收 ycwb 域名的新闻落地页
            if "ycwb.com" not in url:
                continue

            title = self.text_of(a)
            summary = self.text_of(it.css(".c-abstract, .c-abstract p, .c-box"))
            date_text = (self.text_of(it.css("h4")) or "").strip()  # 如 2025-08-14
            ds = fmt_date(parse_date(date_text)) or date_text

            item = self.make_item(
                title=title or url,
                url=url,
                summary=summary,
                source="金羊网",
                date=ds,
                site="ycwb_search",
            )
            if self.validate(item):
                scraped += 1
                yield item

        # ✅ 没有翻页：到这里就结束
        if scraped == 0:
            self.logger.info("YCWB: 搜索页无可用结果（检查关键词或时间范围是否过窄）")
