# -*- coding: utf-8 -*-
# Channel-W: WeChat MP collector via web search (no private API). Franglish comments.

from __future__ import annotations
import re, time
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from crawler_core.scraping.base import BaseCrawler
from crawler_core.utils.search import search_multi
from crawler_core.utils.date_parse import extract_date, within_range

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}
_WECHAT_HOST = "mp.weixin.qq.com"

def _normalize_wx_url(u: str) -> str:
    """
    Normalize MP url for dedup: keep (mid, idx) if present; strip queries/anchors otherwise.
    """
    try:
        pr = urlparse(u)
        if pr.netloc.endswith(_WECHAT_HOST) and pr.path.startswith("/s"):
            qs = parse_qs(pr.query)
            mid = (qs.get("mid") or qs.get("appmsgid") or [""])[0]
            idx = (qs.get("idx") or [""])[0]
            if mid and idx:
                return f"mpwx:mid={mid}&idx={idx}"
    except Exception:
        pass
    # generic strip
    return u.split("#", 1)[0].split("?", 1)[0]

def _first_text(elem: Optional[BeautifulSoup], limit: int = 240) -> str:
    if not elem:
        return ""
    txt = elem.get_text(" ", strip=True)
    return txt[:limit]

class WechatCrawler(BaseCrawler):
    """
    Search-engine driven MP fetcher. Respect rate limit; no login, no API.
    """
    def __init__(self, keywords: Sequence[str], boosters: Optional[Sequence[str]] = None,
                 min_delay_sec: float = 0.6) -> None:
        super().__init__(domain=_WECHAT_HOST, keywords=keywords)
        self.boosters = list(boosters or [])
        self.min_delay = float(min_delay_sec)
        self._last_ts = 0.0

    def _sleep_if_needed(self):
        dt = time.time() - self._last_ts
        if dt < self.min_delay:
            time.sleep(self.min_delay - dt)
        self._last_ts = time.time()

    def _search_urls(self, max_results: int = 24) -> List[str]:
        qs: List[str] = []
        base = " ".join(self.keywords).strip()
        if self.boosters:
            for b in self.boosters:
                qs.append(f"site:{_WECHAT_HOST} {b} {base}")
        else:
            qs.append(f"site:{_WECHAT_HOST} {base}")
        urls: List[str] = []
        for q in qs:
            urls.extend(search_multi(q, max_results=max_results))
        # keep only wechat host; dedup normalized
        seen, out = set(), []
        for u in urls:
            if _WECHAT_HOST not in u:
                continue
            key = _normalize_wx_url(u)
            if key not in seen:
                seen.add(key); out.append(u)
        return out[:max_results]

    def _fetch(self, url: str) -> Optional[str]:
        try:
            self._sleep_if_needed()
            r = requests.get(url, headers=_UA, timeout=12)
            if r.status_code == 200:
                enc = (r.encoding or "").lower()
                if not enc or enc == "iso-8859-1":
                    try:
                        r.encoding = r.apparent_encoding or "utf-8"
                    except Exception:
                        r.encoding = "utf-8"
                return r.text
        except Exception:
            return None
        return None

    def crawl(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, str]]:
        urls = self._search_urls(max_results=36)
        out: List[Dict[str, str]] = []
        for u in urls:
            html = self._fetch(u)
            if not html:
                continue
            d = extract_date(html, u)  # supports publish_time / var ct / json-ld / url
            if (start_date or end_date) and not within_range(d, start_date, end_date):
                continue
            soup = BeautifulSoup(html, "lxml")
            # title
            title = ""
            h1 = soup.select_one("h1#activity-name") or soup.select_one("#activity-name")
            if h1: title = h1.get_text(strip=True)
            if not title and soup.title: title = soup.title.get_text(strip=True)
            # account / author
            account = ""
            a1 = soup.select_one("#js_name") or soup.select_one("#profile_nickname") or soup.find("meta", {"name": "author"})
            if a1:
                account = a1.get_text(strip=True) if hasattr(a1, "get_text") else (a1.get("content") or "")
            # excerpt from content
            content = soup.select_one("#js_content") or soup.find("section")
            excerpt = _first_text(content, 300)
            out.append({
                "title": title or "(untitled)",
                "url": u,
                "date": d or "",
                "excerpt": excerpt,
                "source": account or "WeChat MP"
            })
        return out
