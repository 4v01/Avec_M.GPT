from __future__ import annotations
import re, time, base64
from typing import List, Iterable
from urllib.parse import urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_DEF_TIMEOUT = 12
_SE_DOMAINS = ("bing.com", "duckduckgo.com", "baidu.com", "google.com", "google.com.hk", "so.com", "sogou.com")

def _is_search_engine(host: str) -> bool:
    host = host.lower()
    return any(host.endswith(d) for d in _SE_DOMAINS)

def _norm(u: str) -> str:
    u = u.strip()
    u = re.sub(r"#.*$", "", u)
    return u

def _dedup(urls: Iterable[str], max_results: int) -> List[str]:
    seen, out = set(), []
    for u in urls:
        nu = _norm(u)
        if not nu or nu in seen:
            continue
        seen.add(nu)
        out.append(nu)
        if len(out) >= max_results:
            break
    return out

def _safe_get(url: str, **kw):
    kw.setdefault("timeout", _DEF_TIMEOUT)
    kw.setdefault("headers", {"User-Agent": _UA})
    return requests.get(url, **kw)

def _decode_bing_ck(url: str) -> str:
    """bing.com/ck/a?... 支持 u= / r= 参数；既可能是 urlencoded 也可能是 base64"""
    try:
        qs = parse_qs(urlparse(url).query)
        val = (qs.get("u") or qs.get("r") or [None])[0]
        if not val:
            return url
        val = unquote(val)
        # 可能是 base64(https://...)
        if re.fullmatch(r"[A-Za-z0-9+/=]+", val) and val.startswith(("aHR0", "aHRp")):
            try:
                real = base64.b64decode(val + "==").decode("utf-8", "ignore")
                if real.startswith("http"):
                    return real
            except Exception:
                pass
        if val.startswith("http"):
            return val
        return url
    except Exception:
        return url

def _resolve_wrapped(u: str) -> str:
    """把搜索引擎包装链接解包成真实 URL；解不了就原样返回"""
    try:
        p = urlparse(u)
        host, path = p.netloc.lower(), p.path
        if "duckduckgo.com" in host and path.startswith("/l/"):
            qs = parse_qs(p.query)
            real = qs.get("uddg", [None])[0]
            if real:
                real = unquote(real)
                if real.startswith("http"):
                    return real
        if "bing.com" in host and (path.startswith("/ck/") or path == "/r"):
            real = _decode_bing_ck(u)
            if real.startswith("http"):
                return real
        if "baidu.com" in host and path.startswith("/link"):
            # 直接跟随跳转
            try:
                rr = requests.get(u, headers={"User-Agent": _UA}, timeout=_DEF_TIMEOUT, allow_redirects=True)
                if rr.url and rr.url.startswith("http"):
                    return rr.url
            except Exception:
                pass
        return u
    except Exception:
        return u

# ---------------- DuckDuckGo ----------------
def _ddg(query: str, max_results: int) -> List[str]:
    try:
        r = _safe_get("https://duckduckgo.com/html/", params={"q": query})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        urls: List[str] = []
        for a in soup.select("a.result__a, a.result__a.js-result-title-link"):
            href = a.get("href")
            if not href or not isinstance(href, str):
                continue
            href = str(href)
            if href.startswith("/l/"):
                href = "https://duckduckgo.com" + href
            real = _resolve_wrapped(href)
            host = urlparse(real).netloc
            if real.startswith("http") and not _is_search_engine(host):
                urls.append(real)
        return _dedup(urls, max_results)
    except Exception:
        return []

# ---------------- Bing ----------------
def _bing(query: str, max_results: int) -> List[str]:
    try:
        r = _safe_get("https://www.bing.com/search", params={"q": query, "setlang": "zh-Hans", "ensearch": "1"})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        urls: List[str] = []
        # 正常结果
        for a in soup.select("li.b_algo h2 a, h2 a.btop"):
            href = a.get("href") or ""
            if not href:
                continue
            href = str(href)
            real = _resolve_wrapped(href)
            host = urlparse(real).netloc
            if real.startswith("http") and not _is_search_engine(host):
                urls.append(real)
        # 兜底：把 ck/a 也抓出来解包
        for a in soup.select("a[href^='/ck/'], a[href^='https://www.bing.com/ck/']"):
            real = _resolve_wrapped(str(a.get("href") or ""))            
            host = urlparse(real).netloc
            if real.startswith("http") and not _is_search_engine(host):
                urls.append(real)
        return _dedup(urls, max_results)
    except Exception:
        return []

# ---------------- Baidu ----------------
def _baidu(query: str, max_results: int) -> List[str]:
    try:
        r = _safe_get("https://www.baidu.com/s", params={"wd": query})
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        urls: List[str] = []
        for a in soup.select("#content_left h3 a, #content_left .result h3 a"):
            href = a.get("href") or ""
            if not href:
                continue
            href = str(href)
            real = _resolve_wrapped(href)
            host = urlparse(real).netloc
            if real.startswith("http") and not _is_search_engine(host):
                urls.append(real)
        return _dedup(urls, max_results)
    except Exception:
        return []

def search_multi(query: str, max_results: int = 20) -> List[str]:
    out: List[str] = []
    for fn in (_ddg, _bing, _baidu):
        try:
            part = fn(query, max_results=max_results)
            out.extend(part)
            out = _dedup(out, max_results)
            if len(out) >= max_results:
                break
        except Exception:
            continue
        time.sleep(0.2)
    return out

# 兼容旧 import
def duckduckgo_search(query: str, max_results: int = 10) -> List[str]:
    return search_multi(query, max_results=max_results)
