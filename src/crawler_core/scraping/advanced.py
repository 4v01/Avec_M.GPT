# src/crawler_core/scraping/advanced.py
from __future__ import annotations
from typing import Dict, List, Optional, Sequence
try:
    import curl_cffi.requests as cfre
    _CURL_OK = True
except Exception:
    _CURL_OK = False
from bs4 import BeautifulSoup
from crawler_core.scraping.base import BaseCrawler
from crawler_core.utils.search import search_multi
from crawler_core.utils.date_parse import extract_date

class AdvancedCrawler(BaseCrawler):
    @staticmethod
    def is_available() -> bool:
        return _CURL_OK

    def crawl(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, str]]:
        query = " ".join(self.keywords)
        if self.domain: query = f"site:{self.domain} {query}"
        urls = search_multi(query, max_results=24)
        out: List[Dict[str, str]] = []
        for u in urls:
            html: Optional[str] = None
            if _CURL_OK:
                try:
                    r = cfre.get(u, timeout=12, headers={"User-Agent":"Mozilla/5.0"})
                    if r.status_code == 200: html = r.text
                except Exception:
                    pass
            if not html:
                html = self.fetch(u)
            if not html: 
                continue
            soup = BeautifulSoup(html, "lxml")
            title = (soup.title.string if soup.title else "").strip()
            p = soup.find("p")
            d = extract_date(html, u)
            out.append({
                "title": title, "url": u, "date": d or "",
                "excerpt": p.get_text(strip=True)[:240] if p else "",
                "source": self.domain or ""
            })
        return out
