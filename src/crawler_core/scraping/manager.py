# -*- coding: utf-8 -*-
# crawler_core/scraping/manager.py
# Franglish (ASCII only). Orchestrates channels: search (ch-1), pattern (ch-2), wechat (ch-W).
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# ch-1
from crawler_core.scraping.generic import GenericCrawler
try:
    from crawler_core.scraping.advanced import AdvancedCrawler  # optional
except Exception:  # advanced not available
    AdvancedCrawler = None  # type: ignore

# ch-2 (compat for function-style or class-style)
_PATTERN_MODE = "none"
try:
    from crawler_core.scraping.patterns import run_pattern_crawl, get_rule_for  # type: ignore
    _PATTERN_MODE = "func"
except Exception:
    try:
        from crawler_core.scraping.patterns import PatternCrawler  # type: ignore
        _PATTERN_MODE = "class"
    except Exception:
        PatternCrawler = None  # type: ignore

# ch-W
try:
    from crawler_core.scraping.wechat import WechatCrawler, _normalize_wx_url  # type: ignore
    _WECHAT_READY = True
except Exception:
    WechatCrawler = None  # type: ignore
    def _normalize_wx_url(u: str) -> str: return u
    _WECHAT_READY = False

from crawler_core.utils.site_resolver import SiteResolver
from crawler_core.utils.date_parse import within_range

# --- Heuristics and constants ---
BLOCK_KEYS = {
    "tzgg", "zwgk", "zfxxgk", "gk", "gkml", "bsfw", "zcfg",
    "zhaobiao", "zbgg", "gggs", "gsgg", "jyxx", "xxgk"
}
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}


def _fetch_title_excerpt(url: str) -> Tuple[str, str]:
    """Light fetch for cases where crawlers didn't fill title/excerpt."""
    try:
        r = requests.get(url, headers=_UA, timeout=10)
        if r.status_code != 200:
            return "", ""
        soup = BeautifulSoup(r.text, "lxml")
        title = (soup.title.string if soup.title else "").strip() # type: ignore
        p = soup.find("p")
        excerpt = p.get_text(strip=True)[:240] if p else ""
        return title, excerpt
    except Exception:
        return "", ""


def _looks_relevant(it: dict, keywords: Sequence[str]) -> bool:
    """
    Hard filter: path blacklist + keyword hit + minimal text length.
    - Drop government notices (tzgg/zwgk/...) unless title strongly hits keywords.
    - Require at least one keyword in title or excerpt.
    - Minimal text lengths: title>=6, excerpt>=30.
    """
    url = (it.get("url") or "").lower()
    path = urlparse(url).path if url else ""

    title = it.get("title") or ""
    excerpt = it.get("excerpt") or ""

    # fill if empty (rare)
    if (not title or not excerpt) and url:
        t2, e2 = _fetch_title_excerpt(url)
        title = title or t2
        excerpt = excerpt or e2

    if any(k in path for k in BLOCK_KEYS):
        t_low = title.lower()
        if not any(kw.lower() in t_low for kw in keywords):
            return False

    if keywords:
        combo = f"{title} {excerpt}"
        if not any((kw and kw in combo) for kw in keywords):
            return False

    if len(title.strip()) < 6 or len(excerpt.strip()) < 30:
        return False
    return True


def _rule_based_predict(title: str, excerpt: str, keywords: Sequence[str]) -> int:
    """
    Very light "model-0": if any keyword hits title (strong) or excerpt (weak), label=1 else 0.
    This keeps the ML column meaningful even before a real model is trained.
    """
    t = (title or "")
    e = (excerpt or "")
    for kw in keywords or []:
        if not kw:
            continue
        if kw in t:
            return 1
        if kw in e:
            return 1
    return 0


class CrawlerManager:
    def __init__(self) -> None:
        self.resolver = SiteResolver()

    def _choose_crawler(self, domain: Optional[str], keywords: Sequence[str], use_advanced: bool):
        """Select ch-1 backend: AdvancedCrawler if available and requested; else Generic."""
        if use_advanced and AdvancedCrawler and hasattr(AdvancedCrawler, "is_available") and AdvancedCrawler.is_available():
            return AdvancedCrawler(domain=domain, keywords=keywords)  # type: ignore
        return GenericCrawler(domain=domain, keywords=keywords)

    def _run_pattern(self, domain: str, keywords: Sequence[str],
                     start_date: Optional[str], end_date: Optional[str]) -> List[Dict[str, str]]:
        """Run ch-2 in a mode-agnostic way."""
        items: List[Dict[str, str]] = []
        if _PATTERN_MODE == "func":
            # function-style, safest (no ABC issues)
            try:
                # mypy/pyright ignore if stubs missing
                items = run_pattern_crawl(domain, keywords, start_date, end_date)  # type: ignore
            except Exception:
                items = []
        elif _PATTERN_MODE == "class" and PatternCrawler is not None:
            try:
                if hasattr(PatternCrawler, "is_available_for") and PatternCrawler.is_available_for(domain):  # type: ignore
                    pc = PatternCrawler(domain=domain, keywords=keywords)  # type: ignore
                    items = pc.crawl(start_date=start_date, end_date=end_date)
            except Exception:
                items = []
        return items

    def crawl(
        self,
        keywords: Sequence[str],
        media_names: Optional[Sequence[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        use_advanced: bool = True,
        strict_date: int = 1,
        allow_wechat: bool = False,
    ) -> List[Dict[str, str]]:
        """
        Aggregate:
          - ch-1: search (site:domain + keywords)
          - ch-2: pattern enumeration for known sites
          - ch-W: WeChat (site:mp.weixin.qq.com), gated by allow_wechat
        Then: strict date filter -> relevance filter -> add predicted_label.
        """
        if not keywords or len(list(keywords)) == 0:
            return []

        # 0) resolve candidate domains
        domains: List[Optional[str]] = []
        if media_names:
            for name in media_names:
                try:
                    for d in self.resolver.resolve_multi(name, top_k=3, allow_wechat=allow_wechat):
                        domains.append(d)
                except Exception:
                    continue
            domains = list(dict.fromkeys([d for d in domains if d]))
            if not domains:
                domains = self.resolver.discover_domains_by_keywords(keywords) or [None]  # type: ignore
        else:
            domains = self.resolver.discover_domains_by_keywords(keywords) or [None]  # type: ignore

        results: List[Dict[str, str]] = []
        seen_urls: Set[str] = set()

        # 1) ch-1: per-domain search crawler
        for d in domains:
            crawler = self._choose_crawler(d, keywords, use_advanced)
            try:
                items = crawler.crawl(start_date=start_date, end_date=end_date)
            except Exception:
                items = []
            for it in items:
                u = it.get("url") or ""
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    # ensure minimal fields exist
                    it.setdefault("source", d or "")
                    it.setdefault("channel", "search")
                    results.append(it)

        # 2) strict date (if provided)
        if start_date or end_date:
            filtered: List[Dict[str, str]] = []
            for it in results:
                dstr = (it.get("date") or "").strip() or None
                ok = within_range(dstr, start_date, end_date)
                if ok or (strict_date == 0 and dstr is None):
                    filtered.append(it)
            results = filtered

        # 2.5) ch-2: pattern enumeration
        # Prefer when we have concrete domains, best-effort if not.
        if domains:
            for d in domains:
                if not d:
                    continue
                try:
                    items2 = self._run_pattern(d, keywords, start_date, end_date)
                except Exception:
                    items2 = []
                for it in items2:
                    u = it.get("url") or ""
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        it.setdefault("source", d)
                        it.setdefault("channel", "pattern")
                        results.append(it)

        # 3) ch-W: WeChat, when allowed
        if allow_wechat and _WECHAT_READY:
            boosters = list(media_names or [])
            wc = WechatCrawler(keywords=keywords, boosters=boosters, min_delay_sec=0.6)  # type: ignore
            try:
                witems = wc.crawl(start_date=start_date, end_date=end_date)
            except Exception:
                witems = []
            seen_keys: Set[str] = set(_normalize_wx_url(u) for u in seen_urls)
            for it in witems:
                u = it.get("url") or ""
                k = _normalize_wx_url(u)
                if u and (u not in seen_urls) and (k not in seen_keys):
                    seen_urls.add(u); seen_keys.add(k)
                    it.setdefault("source", "WeChat MP")
                    it.setdefault("channel", "wechat")
                    results.append(it)

        # 4) final relevance filter (path blacklist + keyword hit + length)
        if keywords:
            results = [it for it in results if _looks_relevant(it, keywords)]

        # 5) add a light predicted_label so the ML column is visible even without model
        for it in results:
            t = it.get("title") or ""
            e = it.get("excerpt") or ""
            it.setdefault("predicted_label", _rule_based_predict(t, e, keywords)) # pyright: ignore[reportArgumentType]

        return results
