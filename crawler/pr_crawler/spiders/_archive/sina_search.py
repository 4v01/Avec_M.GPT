# -*- coding: utf-8 -*-
import scrapy, urllib.parse
from bs4 import BeautifulSoup
from dateparser import parse as dparse

class SinaSearchSpider(scrapy.Spider):
    name = "sina_search"
    custom_settings = {"DOWNLOAD_DELAY": 0.5}
    def __init__(self, keywords="", *a, **kw):
        super().__init__(*a, **kw); self.kw=keywords

    def start_requests(self):
        q = urllib.parse.quote(self.kw)
        url = f"https://search.sina.com.cn/?c=news&q={q}"
        yield scrapy.Request(url, callback=self.parse)

    def parse(self, resp):
        soup = BeautifulSoup(resp.text, "lxml")
        for blk in soup.select(".box-result, .r-info, .box-result.clearfix"):
            a = blk.select_one("h2 a")
            t = (a.get_text(strip=True) if a else "").strip()
            u = (a.get("href","").strip() if a else "")
            meta = (blk.select_one(".fgray_time") or blk).get_text(" ", strip=True)
            dt = None
            for tok in meta.split():
                try:
                    dt = dparse(tok)
                    if dt: break
                except: pass
            if t and u:
                yield {"title": t, "url": u, "source": "SinaSearch", "published": dt.isoformat() if dt else None}
