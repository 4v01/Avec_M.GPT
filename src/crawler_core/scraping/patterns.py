# -*- coding: utf-8 -*-
# Channel-2: pattern enumeration with node_* & list-like pages -> content_* discovery.
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple
import re
from urllib.parse import urlparse, urljoin
from datetime import datetime, timedelta

# fast HTTP
try:
    import curl_cffi.requests as cfre
    _CURL_OK = True
except Exception:
    _CURL_OK = False

import requests
from bs4 import BeautifulSoup

from crawler_core.scraping.base import BaseCrawler
from crawler_core.utils.date_parse import within_range

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}

# ---------- heuristics ----------
NEWS_KEYS = [
    "xw","news","xwdt","gzdt","yaowen","yw","xwzx","xinwen","zxxw","rdxw",
    "html5","h5","content","gzdaily","channel","guangzhou","politics","finance","paper","yuanchuang"
]
BLOCK_KEYS = ["tzgg","zwgk","zfxxgk","gkml","gk","bsfw","zcfg","zhaobiao","zbgg","gggs","gsgg","jyxx","xxgk"]

DATE_PAT_URL = [
    re.compile(r"/20\d{2}[-/]?(0[1-9]|1[0-2])[-/]?(0[1-9]|[12]\d|3[01])/", re.I),
    re.compile(r"/n\d/20\d{2}/(0[1-9]|1[0-2])(0[1-9]|[12]\d)/", re.I),  # people.com.cn
    re.compile(r"/20\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])/", re.I),
]
DATE_PAT_HTML = [
    re.compile(r"(20\d{2})[.\-/年](0[1-9]|1[0-2])[.\-/月](0[1-9]|[12]\d|3[01])[日]?", re.I),
    re.compile(r"(20\d{2})[.\-/](0[1-9]|1[0-2])[.\-/](0[1-9]|[12]\d|3[01])", re.I),
]

NODE_RE      = re.compile(r"node_\d+(?:_\d+)?\.htm", re.I)
LIST_LIKE_RE = re.compile(r"/\d{5,}\.s?html?$", re.I)            # .../guangzhou/139995.shtml
CONTENT_RE   = re.compile(r"content_\d+_\d+\.htm", re.I)
CONTENT_ANY  = re.compile(r"(?:https?:\/\/[^\"'#\s]+)?content_\d+_\d+\.htm", re.I)  # 包含脚本字符串里的

def _host(h: str) -> str:
    h = (h or "").lower().strip()
    return re.sub(r"^(www|m|mp|wap)\.", "", h)

def _same_site_multi(url: str, accepted: Sequence[str]) -> bool:
    try:
        netloc = _host(urlparse(url).netloc)
        return any(netloc.endswith(_host(d)) for d in accepted)
    except Exception:
        return False

def _is_news_path(path: str) -> bool:
    p = (path or "").lower()
    if any(b in p for b in BLOCK_KEYS): return False
    return any(k in p for k in NEWS_KEYS)

def _fetch(url: str, timeout: int = 12) -> Optional[str]:
    try:
        if _CURL_OK:
            r = cfre.get(url, headers=_UA, timeout=timeout)
            if r.status_code == 200: return r.text
        r2 = requests.get(url, headers=_UA, timeout=timeout)
        if r2.status_code == 200:
            enc = (r2.encoding or "").lower()
            if not enc or enc == "iso-8859-1":
                try: r2.encoding = r2.apparent_encoding or "utf-8"
                except Exception: r2.encoding = "utf-8"
            return r2.text
    except Exception:
        pass
    return None

def _date_from_url(u: str) -> Optional[str]:
    for rp in DATE_PAT_URL:
        m = rp.search(u)
        if m:
            y = re.search(r"20\d{2}", m.group(0))
            parts = re.findall(r"(0[1-9]|1[0-2]|[12]\d|3[01])", m.group(0))
            if y and len(parts) >= 2:
                return f"{y.group(0)}-{parts[0]}-{parts[1]}"
    return None

def _date_from_html(html: str) -> Optional[str]:
    try:
        soup = BeautifulSoup(html, "lxml")
        for key in ("pubdate","publishdate","date","ptime","PublishTime"):
            node = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
            if node and node.get("content"):
                txt = node["content"]
                for rp in DATE_PAT_HTML:
                    m = rp.search(txt)
                    if m: y,mm,dd = m.groups(); return f"{y}-{mm}-{dd}"
        txt = soup.get_text(" ", strip=True)[:2000]
        for rp in DATE_PAT_HTML:
            m = rp.search(txt)
            if m: y,mm,dd = m.groups(); return f"{y}-{mm}-{dd}"
    except Exception:
        pass
    return None

# ---------- rules ----------
@dataclass
class PatternRule:
    domain: str
    seeds: Sequence[str]
    link_allow: re.Pattern
    max_links: int = 280
    max_per_seed: int = 110
    enable_node_mode: bool = False
    enable_list_mode: bool = True
    accepted_domains: Sequence[str] = ()

    @property
    def domains(self) -> Sequence[str]:
        return self.accepted_domains or (self.domain,)

DAYOO_RULE = PatternRule(
    domain="dayoo.com",
    seeds=("https://news.dayoo.com/guangzhou/","https://news.dayoo.com/","https://gzdaily.dayoo.com/"),
    link_allow=re.compile(r"/20\d{2}[-/]?(0[1-9]|1[0-2])[-/]?(0[1-9]|[12]\d|3[01])/.+?\.htm[l]?$", re.I),
    accepted_domains=("dayoo.com","news.dayoo.com","gzdaily.dayoo.com"),
)
GZDAILY_RULE = PatternRule(
    domain="gzdaily.dayoo.com",
    seeds=("https://gzdaily.dayoo.com/h5/html5/","https://gzdaily.dayoo.com/","https://news.dayoo.com/guangzhou/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/.+?\.htm[l]?$", re.I),
    enable_node_mode=True,
    accepted_domains=("gzdaily.dayoo.com","news.dayoo.com","dayoo.com"),
)
PEOPLE_RULE = PatternRule(
    domain="people.com.cn",
    seeds=("https://people.com.cn/","https://politics.people.com.cn/","https://cpc.people.com.cn/"),
    link_allow=re.compile(r"/n\d/20\d{2}/(0[1-9]|1[0-2])(0[1-9]|[12]\d)/.+?\.html?$", re.I),
)
XINHUA_RULE = PatternRule(
    domain="news.cn",
    seeds=("https://www.news.cn/","https://www.news.cn/politics/","https://www.news.cn/local/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/.+?\.htm[l]?$", re.I),
)
SOUTHCN_RULE = PatternRule(
    domain="southcn.com",
    seeds=("https://www.southcn.com/","https://news.southcn.com/","https://news.southcn.com/gd/"),
    link_allow=re.compile(r"/content/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/.+?\.(htm|html)$", re.I),
)
NFNEWS_RULE = PatternRule(
    domain="nfnews.com",
    seeds=("https://www.nfnews.com/","https://pc.nfapp.southcn.com/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/", re.I),
)
YCWB_RULE = PatternRule(
    domain="ycwb.com",
    seeds=("https://news.ycwb.com/","https://news.ycwb.com/guangzhou/","https://news.ycwb.com/yuanchuang/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/.+?\.(htm|html)$", re.I),
)
NANDU_RULE = PatternRule(
    domain="nandu.com",
    seeds=("https://www.nandu.com/","https://www.nandu.com/news/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/", re.I),
)
XKB_RULE = PatternRule(
    domain="xkb.com.cn",
    seeds=("https://news.xkb.com.cn/","https://xkb.com.cn/"),
    link_allow=re.compile(r"/20\d{2}[-/]?(0[1-9]|1[0-2])[-/]?(0[1-9]|[12]\d|3[01])/", re.I),
)
CNSTOCK_RULE = PatternRule(
    domain="cnstock.com",
    seeds=("https://www.cnstock.com/","https://news.cnstock.com/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/.+?\.(htm|html)$", re.I),
)
CNR_RULE = PatternRule(
    domain="cnr.cn",
    seeds=("https://www.cnr.cn/","https://news.cnr.cn/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/", re.I),
)
GZ_GOV_RULE = PatternRule(
    domain="gz.gov.cn",
    seeds=("https://www.gz.gov.cn/xw/","https://www.gz.gov.cn/"),
    link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/", re.I),
)

_GZ_DISTRICTS = [
    "yuexiu.gov.cn","liwan.gov.cn","haizhu.gov.cn","tianhe.gov.cn","baiyun.gov.cn",
    "huadu.gov.cn","panyu.gov.cn","nansha.gov.cn","conghua.gov.cn","zengcheng.gov.cn",
    "gdd.gov.cn","huangpu.gov.cn",
]

_RULES: Dict[str, PatternRule] = {
    "dayoo.com": DAYOO_RULE,
    "gzdaily.dayoo.com": GZDAILY_RULE,
    "people.com.cn": PEOPLE_RULE,
    "news.cn": XINHUA_RULE,
    "southcn.com": SOUTHCN_RULE,
    "nfnews.com": NFNEWS_RULE,
    "ycwb.com": YCWB_RULE,
    "nandu.com": NANDU_RULE,
    "xkb.com.cn": XKB_RULE,
    "cnstock.com": CNSTOCK_RULE,
    "cnr.cn": CNR_RULE,
    "gz.gov.cn": GZ_GOV_RULE,
}
for d in _GZ_DISTRICTS:
    _RULES[d] = PatternRule(
        domain=d,
        seeds=(f"https://www.{d}/",),
        link_allow=re.compile(r"/20\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])/", re.I),
        max_links=160, max_per_seed=60,
        enable_list_mode=False,
    )

def get_rule_for(domain: str) -> Optional[PatternRule]:
    d = _host(domain)
    best_key = None
    for k in _RULES.keys():
        if d.endswith(_host(k)):
            if best_key is None or len(_host(k)) > len(_host(best_key)):
                best_key = k
    return _RULES.get(best_key) if best_key else None

def _guess_generic_rule(domain: str) -> PatternRule:
    seeds = [
        f"https://{domain}/", f"https://www.{domain}/",
        f"https://{domain}/news/", f"https://www.{domain}/news/",
        f"https://{domain}/xw/", f"https://{domain}/xwdt/",
        f"https://{domain}/content/", f"https://{domain}/h5/html5/",
    ]
    allow = re.compile(r"/20\d{2}[-/]?(0[1-9]|1[0-2])[-/]?(0[1-9]|[12]\d|3[01])/", re.I)
    return PatternRule(
        domain=domain, seeds=tuple(seeds), link_allow=allow,
        max_links=180, max_per_seed=70,
        enable_node_mode=True, enable_list_mode=True
    )

class PatternCrawler(BaseCrawler):
    def __init__(self, domain: str, keywords: Sequence[str]) -> None:
        super().__init__(domain=domain, keywords=keywords)
        self.rule: PatternRule = get_rule_for(domain) or _guess_generic_rule(domain)

    @staticmethod
    def is_available_for(domain: Optional[str]) -> bool:
        return bool(domain)

    def _get_html(self, url: str) -> Optional[str]:
        try:
            fetch = getattr(super(), "fetch", None)  # type: ignore
        except Exception:
            fetch = None
        if callable(fetch):
            try: return fetch(url)  # type: ignore
            except Exception: return _fetch(url)
        return _fetch(url)

    def _extract_links(self, base: str, html: str) -> Tuple[List[str], List[str], List[str]]:
        """Return (article_urls, node_urls, list_like_urls)."""
        soup = BeautifulSoup(html, "lxml")
        arts: List[str] = []; nodes: List[str] = []; lists: List[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("//"): href = "https:" + href
            elif href.startswith("/"): href = urljoin(base, href)
            if not href.startswith("http"): continue
            if not _same_site_multi(href, self.rule.domains): continue

            path = urlparse(href).path.lower()

            if self.rule.enable_node_mode and NODE_RE.search(path):
                nodes.append(href); continue

            if self.rule.enable_list_mode and LIST_LIKE_RE.search(path) and _is_news_path(path):
                lists.append(href); continue

            ok = False
            if CONTENT_RE.search(path): ok = True
            elif self.rule.link_allow.search(href): ok = True
            elif any(p.search(href) for p in DATE_PAT_URL): ok = True
            if not ok:  # 列表页不直接当文章
                continue

            arts.append(href)
        return arts, nodes, lists

    def _iter_node_pages(self, node_url: str, max_pages: int = 8) -> List[str]:
        out = [node_url]
        m = re.search(r"(node_\d+)(?:_\d+)?\.htm", node_url)
        if not m: return out
        head = m.group(1)
        root = node_url.rsplit("/", 1)[0] + "/"
        for i in range(2, max_pages + 1):
            out.append(urljoin(root, f"{head}_{i}.htm"))
        return out

    def _expand_list_like(self, list_url: str, html: str) -> List[str]:
        """从聚合页中抠出 content_* 链接；除了 <a> 之外，还对整页做正则扫描（兼容脚本注入）。"""
        arts, _, _ = self._extract_links(list_url, html)
        if arts:
            return arts
        out: List[str] = []
        base = f"{urlparse(list_url).scheme}://{urlparse(list_url).netloc}/"
        # 扫脚本/字符串里的 content_*.htm
        for m in CONTENT_ANY.findall(html or ""):
            u = m
            if not u.startswith("http"):
                u = urljoin(base, u)
            if _same_site_multi(u, self.rule.domains) and u not in out:
                out.append(u)
        return out

    def _probe_dayoo_h5_by_dates(self, start_date: Optional[str], end_date: Optional[str]) -> List[str]:
        """专项：直探 gzdaily.h5 日期目录，把 content_*.htm 扫出来。"""
        accepted = set(self.rule.domains)
        if not any("dayoo.com" in d for d in accepted):
            return []
        if not start_date or not end_date:
            return []
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d")
            ed = datetime.strptime(end_date, "%Y-%m-%d")
        except Exception:
            return []
        if ed < sd: sd, ed = ed, sd
        out: List[str] = []
        cur = sd
        while cur <= ed:
            d = cur.strftime("%Y-%m/%d")
            idx = f"https://gzdaily.dayoo.com/h5/html5/{d}/"
            html = _fetch(idx)
            if html:
                for m in CONTENT_ANY.findall(html):
                    u = m
                    if not u.startswith("http"):
                        u = urljoin("https://gzdaily.dayoo.com/h5/html5/", f"{d}/{m}")
                    if _same_site_multi(u, self.rule.domains) and u not in out:
                        out.append(u)
            cur += timedelta(days=1)
        return out

    def _collect_links(self, rule: PatternRule,
                       start_date: Optional[str],
                       end_date: Optional[str]) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        node_queue: List[str] = []
        list_queue: List[str] = []

        # 1) seeds
        for seed in rule.seeds:
            html = _fetch(seed)
            if not html: continue
            arts, nodes, lists = self._extract_links(seed, html)
            for u in arts:
                if u not in seen:
                    seen.add(u); out.append(u)
                    if len(out) >= rule.max_links: return out
            for n in nodes:
                if n not in seen:
                    seen.add(n); node_queue.append(n)
            for l in lists:
                if l not in seen:
                    seen.add(l); list_queue.append(l)

        # 2) node pages
        if rule.enable_node_mode and node_queue:
            for n in node_queue[:30]:
                for page in self._iter_node_pages(n, max_pages=8):
                    html = _fetch(page)
                    if not html: continue
                    arts, _, _ = self._extract_links(page, html)
                    for u in arts:
                        if u not in seen:
                            seen.add(u); out.append(u)
                            if len(out) >= rule.max_links: return out

        # 3) list-like pages（包括脚本里隐藏的 content_）
        if rule.enable_list_mode and list_queue:
            for l in list_queue[:50]:
                html = _fetch(l)
                if not html: continue
                arts = self._expand_list_like(l, html)
                for u in arts:
                    if u not in seen:
                        seen.add(u); out.append(u)
                        if len(out) >= rule.max_links: return out

        # 4) Dayoo/H5 专项兜底：按日期目录直探
        if not out and any("dayoo.com" in d for d in rule.domains):
            for u in self._probe_dayoo_h5_by_dates(start_date, end_date):
                if u not in seen:
                    seen.add(u); out.append(u)
                    if len(out) >= rule.max_links: break

        return out

    def crawl(self, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, str]]:
        urls = self._collect_links(self.rule, start_date, end_date)
        results: List[Dict[str, str]] = []
        for u in urls:
            d = _date_from_url(u)
            if (start_date or end_date) and d and not within_range(d, start_date, end_date):
                continue

            html = self._get_html(u)
            if not html: continue

            if not d:
                d = _date_from_html(html)
                if (start_date or end_date) and d and not within_range(d, start_date, end_date):
                    continue
            elif (start_date or end_date) and not within_range(d, start_date, end_date):
                d2 = _date_from_html(html)
                if not within_range(d2, start_date, end_date): continue
                d = d2 or d

            soup = BeautifulSoup(html, "lxml")
            title = (soup.title.string if soup.title else "").strip()
            p = soup.find("p")
            excerpt = p.get_text(strip=True)[:240] if p else ""
            results.append({
                "title": title, "url": u, "date": d or "", "excerpt": excerpt,
                "source": self.rule.domain or "", "channel": "pattern",
            })
        return results

def run_pattern_crawl(domain: str, keywords: Sequence[str],
                      start_date: Optional[str], end_date: Optional[str]) -> List[Dict[str, str]]:
    pc = PatternCrawler(domain=domain, keywords=keywords)
    return pc.crawl(start_date=start_date, end_date=end_date)
