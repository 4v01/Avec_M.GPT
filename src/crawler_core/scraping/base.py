from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Sequence
import requests
from bs4 import BeautifulSoup
from bs4 import Tag
logger = logging.getLogger(__name__)

class BaseCrawler(ABC):
    def __init__(self, domain: Optional[str] = None, keywords: Optional[Sequence[str]] = None) -> None:
        self.domain = domain
        self.keywords = list(keywords or [])

    @abstractmethod
    def crawl(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, str]]:
        raise NotImplementedError

    def fetch(self, url: str) -> Optional[str]:
        try:
            headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
            r = requests.get(url, timeout=12, headers=headers)
            if r.status_code != 200:
                return None
            # 纠正编码
            enc = (r.encoding or "").lower()
            if not enc or enc == "iso-8859-1":
                try:
                    r.encoding = r.apparent_encoding or "utf-8"
                except Exception:
                    r.encoding = "utf-8"
            return r.text
        except Exception:
            return None
    def parse_basic(self, html: str) -> Dict[str, str]:
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.title
        title = title_tag.string.strip() if title_tag and title_tag.string else ""
        if not title:
            m = soup.find("meta", attrs={"property":"og:title"}) or soup.find("meta", attrs={"name":"og:title"})
            if isinstance(m, Tag) and m.get("content"):
                title = str(m["content"]).strip()
        p = soup.find("p")
        return {"title": title, "excerpt": p.get_text(strip=True)[:240] if p else ""}
