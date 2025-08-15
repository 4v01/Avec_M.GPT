# src/crawler_core/utils/date_parse.py
from __future__ import annotations
import re, json
from datetime import datetime, timezone
from typing import Optional, Mapping
from bs4 import BeautifulSoup, Tag

_DATE_PAT = re.compile(
    r"(?:(20\d{2})[-/\.](0?[1-9]|1[0-2])[-/\.](0?[1-9]|[12]\d|3[01]))"  # 2025-08-14 / 2025/08/14 / 2025.08.14
)
_DATE_CN = re.compile(r"(20\d{2})年(0?[1-9]|1[0-2])月(0?[1-9]|[12]\d|3[01])日")
_DATE_URL = re.compile(r"/(20\d{2})[/-]?(0[1-9]|1[0-2])[/-]?(0[1-9]|[12]\d|3[01])")

def _pad(y: str, m: str, d: str) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

def _from_json_ld(soup: BeautifulSoup) -> Optional[str]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(getattr(tag, 'string', '') or "")
            # could be dict or list
            arr = data if isinstance(data, list) else [data]
            for obj in arr:
                for key in ("datePublished", "dateModified", "uploadDate"):
                    val = obj.get(key)
                    if isinstance(val, str) and val[:4].isdigit():
                        m = _DATE_PAT.search(val)
                        if m:
                            return _pad(*m.groups())
        except Exception:
            continue
    return None


def _from_meta(soup: BeautifulSoup) -> Optional[str]:
    keys: list[tuple[str, dict[str, str]]] = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "pubdate"}),
        ("meta", {"name": "publishdate"}),
        ("meta", {"property": "og:updated_time"}),
        ("meta", {"name": "publish_time"}),
        ("meta", {"itemprop": "datePublished"}),
    ]
    for tagname, attrs in keys:
        tag = soup.find(tagname, attrs=attrs)
        if isinstance(tag, Tag):  # 确保是 Tag 类型再调用 get
            content = tag.get("content")
            if isinstance(content, str):
                m = _DATE_PAT.search(content)
                if m:
                    return _pad(*m.groups())
    # fallback: visible text (small cost)
    text = soup.get_text(" ", strip=True)[:4000]
    m = _DATE_CN.search(text) or _DATE_PAT.search(text)
    if m:
        return _pad(*m.groups())
    return None

def extract_date(html: str, url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")

    d = _from_json_ld(soup)
    if d: return d

    d = _from_meta(soup)
    if d: return d

    # WeChat special
    if "mp.weixin.qq.com" in url:
        try:
            em = soup.find(id="publish_time")
            if em:
                tx = em.get_text(strip=True)
                m = _DATE_PAT.search(tx)
                if m: return _pad(*m.groups())
            m2 = re.search(r"""var\s+ct\s*=\s*['"](\d{10})['"]""", html)
            if m2:
                ts = int(m2.group(1))
                d2 = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                return d2.strftime("%Y-%m-%d")
        except Exception:
            pass

    m = _DATE_URL.search(url)
    if m: return _pad(*m.groups())
    return None


def within_range(dstr: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    if not dstr:
        return False  # strict mode: no date -> drop
    try:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
    except Exception:
        return False
    if start:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        if d < s:
            return False
    if end:
        e = datetime.strptime(end, "%Y-%m-%d").date()
        if d > e:
            return False
    return True
