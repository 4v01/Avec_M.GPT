# src/crawler_core/scraping/generic.py
from __future__ import annotations
from typing import Dict, List, Optional, Sequence
from bs4 import BeautifulSoup
from crawler_core.scraping.base import BaseCrawler
from crawler_core.utils.search import search_multi
from crawler_core.utils.date_parse import extract_date

def _meta(soup: BeautifulSoup, names) -> str:
    for nm in names:
        tag = soup.find("meta", attrs={"name": nm}) or soup.find("meta", attrs={"property": nm})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""

class GenericCrawler(BaseCrawler):
    def __init__(self, domain: Optional[str], keywords: Sequence[str]) -> None:
        super().__init__(domain, keywords)

    def crawl(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, str]]:
        query = " ".join(self.keywords)
        if self.domain:
            query = f"site:{self.domain} {query}"
        urls = search_multi(query, max_results=24)
        out: List[Dict[str, str]] = []
        for u in urls:
            html = self.fetch(u)
            if not html:
                continue
            soup = BeautifulSoup(html, "lxml")
            title = (soup.title.string if soup.title else "").strip()
            if not title:
                title = _meta(soup, ["og:title", "twitter:title"]) or ""
            excerpt = ""
            ogd = _meta(soup, ["og:description", "description", "twitter:description"])
            if ogd:
                excerpt = ogd[:240]
            else:
                p = soup.find("p")
                excerpt = (p.get_text(strip=True)[:240] if p else "")
            d = extract_date(html, u)
            source = _meta(soup, ["og:site_name"]) or (self.domain or "")
            out.append({"title": title, "url": u, "date": d or "", "excerpt": excerpt, "source": source})
        return out
