# -*- coding: utf-8 -*-
"""
新闻爬虫 · 轻后端（单文件版）
- /crawl              : 执行爬取（normal/boost/browser）
- /review             : 接收人工判定，导出 CSV，写入 SQLite
- /export/xlsx_template: 导出宣传部 Excel 模板（或 CSV 回退）
- /download/<path>    : 静态下载
- /                   : 端出前端 index.html（支持 CRAWLER_FE_DIR）

作者：Avol
"""

from __future__ import annotations
import os
import re
import csv
import io
import json
import html
import uuid
import time
import sqlite3
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
from urllib.parse import urlparse, urljoin, quote

from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS

# -------------------------- 日志 --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("crawler_core.api.app")

# -------------------------- 可选依赖 --------------------------
HAS_CURL = False
HAS_LXML = False
HAS_BS4 = False
HAS_DDG = False
HAS_PW = False
HAS_OX = False  # openpyxl

try:
    from curl_cffi import requests as cffi_requests  # type: ignore
    HAS_CURL = True
except Exception:
    pass

try:
    import lxml.html as LH  # type: ignore
    HAS_LXML = True
except Exception:
    pass

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

try:
    from bs4 import BeautifulSoup  # type: ignore
    HAS_BS4 = True
except Exception:
    pass

try:
    from duckduckgo_search import DDGS  # type: ignore
    HAS_DDG = True
except Exception:
    pass

try:
    from playwright.sync_api import sync_playwright  # type: ignore
    HAS_PW = True
except Exception:
    pass

try:
    import openpyxl  # type: ignore
    from openpyxl.styles import Alignment, Font
    HAS_OX = True
except Exception:
    pass

# -------------------------- Flask 基本盘 --------------------------
app = Flask(__name__, static_folder=None)
CORS(app)

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
# 项目根（/src/crawler_core/api -> 上两级）
PROJ_ROOT = os.path.abspath(os.path.join(ROOT_DIR, "..", "..", ".."))
DATA_DIR = os.path.join(PROJ_ROOT, "data")
EXPORT_DIR = os.path.join(PROJ_ROOT, "exports")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "crawler.db")

# -------------------------- 工具函数 --------------------------
def _norm_ymd(y: str, m: str, d: str) -> str:
    y = y.strip(); m = m.strip(); d = d.strip()
    if len(m) == 1: m = "0" + m
    if len(d) == 1: d = "0" + d
    return f"{y}-{m}-{d}"

def extract_date_fuzzy(text: str, url: str = "") -> str:
    """
    从正文/标题/URL 模糊提取日期，返回 YYYY-MM-DD 或 ""。
    覆盖：
    - 2025-8-14 / 2025/08/14 / 2025.08.14
    - /202508/14/   （dayoo h5 类）
    - 20250814      （连写）
    - 2025年08月14日
    """
    s = (text or "") + " " + (url or "")
    s = s.replace("年", "-").replace("月", "-").replace("日", "-").replace(".", "-").replace("/", "-")
    # 1) 直接 yyyy-mm-dd
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _norm_ymd(m.group(1), m.group(2), m.group(3))
    # 2) URL 里的 yyyy-mm / dd 分段：.../2025-08-14/ 或 .../2025-08/.../14/
    m = re.search(r"(20\d{2})-(\d{1,2}).*?-(\d{1,2})", s)
    if m:
        return _norm_ymd(m.group(1), m.group(2), m.group(3))
    # 3) 连写 yyyyMMdd
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", s)
    if m:
        return _norm_ymd(m.group(1), str(int(m.group(2))), str(int(m.group(3))))
    return ""

def strip_html(text: str) -> str:
    t = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    t = re.sub(r"(?is)<style.*?>.*?</style>", " ", t)
    t = re.sub(r"(?is)<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def any_kw_in(s: str, kws: List[str]) -> bool:
    if not kws:
        return True
    s_low = s.lower()
    for k in kws:
        if k and k.lower() in s_low:
            return True
    return False

def to_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i).strip() for i in x if str(i).strip()]
    s = str(x).strip()
    return [i.strip() for i in s.split(",") if i.strip()]

def reg_domain(host: str) -> str:
    host = (host or "").lower()
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"com", "net", "org", "gov", "edu"} and parts[-1] == "cn":
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host

def widen_domains(domains: List[str]) -> List[str]:
    out = set()
    for d in domains:
        d = (d or "").strip().lower()
        if not d:
            continue
        out.add(d)
        out.add(reg_domain(d))
    return list(out)

def ensure_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        title TEXT,
        url TEXT,
        source TEXT,
        date TEXT,
        predicted_label TEXT,
        human_label TEXT,
        created_at INTEGER
    )
    """)
    con.commit()
    con.close()

ensure_db()

# -------------------------- 站点别名 → 域名 --------------------------
ALIASES: Dict[str, str] = {
    # 重点（你这次项目常用）
    "大洋网": "dayoo.com",
    "广州日报": "gzdaily.dayoo.com",
    "南方+": "southcn.com",
    "南方日报": "southcn.com",
    "羊城晚报": "ycwb.com",
    "新快报": "xkb.com.cn",
    "信息时报": "informationtimes.com",
    "南方都市报": "southernmetropolis.com",  # 有时也归属 southcn
    "广州台": "gztv.com",
    "广东台": "gdtv.cn",
    "广州越秀发布": "yuexiu.gov.cn",
    "越秀发布": "yuexiu.gov.cn",
    "南方+": "southcn.com",

    # 中央/全国
    "央视": "cctv.com",
    "央广网": "cnr.cn",
    "人民网": "people.com.cn",
    "新华网": "xinhuanet.com",
    "中新社": "chinanews.com.cn",
    "中国网": "china.com.cn",
    "中国企业报": "ce.cn",
    "光明网": "gmw.cn",
}

# 各家“正文页 URL”形态（可以后续继续补）
ARTICLE_PATTERNS: Dict[str, List[re.Pattern]] = {
    # 大洋网 / 广州日报
    "dayoo.com": [
        re.compile(r'/h5/html5/20\d{2}-\d{2}/\d{2}/content[_\d]+\.htm', re.I),
        re.compile(r'/pc/html/20\d{2}-\d{2}/\d{2}/content[_\d]+\.htm', re.I),
        re.compile(r'/20\d{2}-\d{2}/\d{2}/content[_\d]+\.htm', re.I),
        re.compile(r'/\d{6}/\d{2}/\d+\.s?html', re.I),
    ],
    "gzdaily.dayoo.com": [
        re.compile(r'/h5/html5/20\d{2}-\d{2}/\d{2}/content[_\d]+\.htm', re.I),
        re.compile(r'/pc/html/20\d{2}-\d{2}/\d{2}/content[_\d]+\.htm', re.I),
    ],

    # 南方+（南方网）
    "southcn.com": [
        re.compile(r'/content/20\d{2}-\d{2}/\d{2}/content_\d+\.htm', re.I),
        re.compile(r'/20\d{2}-\d{2}/\d{2}/content_\d+\.htm', re.I),
    ],
    "nfnews.com": [
        re.compile(r'static\.nfnews\.com/content/20\d{2}\d{2}/[a-z0-9]+\.html', re.I),
        re.compile(r'/content/20\d{2}\d{2}/[a-z0-9]+\.html', re.I),
    ],

    # 羊城晚报/金羊网
    "ycwb.com": [
        re.compile(r'/20\d{2}-\d{2}/\d{2}/content_\d+\.htm[l]?', re.I),
        re.compile(r'/20\d{2}-\d{2}/\d{2}/\d+\.htm[l]?', re.I),
    ],

    # 省台/市台（形态多，先笼统）
    "gdtv.cn": [
        re.compile(r'/\d{4}/\d{2}/\d{2}/\w+\.s?html', re.I),
        re.compile(r'/pl/\w+', re.I),
    ],
    "gztv.com": [
        re.compile(r'/plushare/.*', re.I),
        re.compile(r'/\d{4}/\d{2}/\d{2}/\w+\.s?html', re.I),
    ],

    # 央媒
    "people.com.cn": [
        re.compile(r'/n\d/20\d{2}/\d{4}/c\d+-\d+\.html', re.I),
        re.compile(r'/20\d{2}/\d{2}/\d{2}/c\d+-\d+\.html', re.I),
    ],
    "xinhuanet.com": [
        re.compile(r'/20\d{2}-\d{2}/\d{2}/c_.*?\.htm', re.I),
        re.compile(r'/20\d{2}-\d{2}/\d{2}/\w+_\d+\.htm', re.I),
    ],
    "chinanews.com.cn": [
        re.compile(r'/\w+/20\d{2}/\d{2}-\d{2}/\d+\.shtml', re.I),
    ],
    "gmw.cn": [
        re.compile(r'/20\d{2}-\d{2}/\d{2}/content_\d+\.htm', re.I),
        re.compile(r'/20\d{2}-\d{2}/\d{2}/\w+_\d+\.htm', re.I),
    ],
    "china.com.cn": [
        re.compile(r'/20\d{2}-\d{2}/\d{2}/content_\d+\.htm', re.I),
    ],
    "ce.cn": [
        re.compile(r'/20\d{2}-\d{2}/\d{2}/\w+_\d+\.s?html', re.I),
    ],
}


DEFAULT_SITES = [
    "dayoo.com", "gzdaily.dayoo.com",
    "southcn.com", "ycwb.com", "nfnews.com",
    "gztv.com", "gdtv.cn",
    "people.com.cn", "xinhuanet.com", "chinanews.com.cn",
    "ce.cn", "gmw.cn", "china.com.cn",
]

def resolve_media_to_domains(media_names: List[str]) -> List[str]:
    if not media_names:
        return DEFAULT_SITES.copy()
    out = []
    for name in media_names:
        name = name.strip()
        if not name:
            continue
        if re.search(r"\w+\.\w+", name):
            out.append(name.lower())
        elif name in ALIASES:
            out.append(ALIASES[name])
        else:
            # 模糊：可能是“××日报”，尽量猜一个
            if "大洋" in name or "广州日报" in name:
                out.append("dayoo.com")
            elif "南方" in name:
                out.append("southcn.com")
            elif "羊城" in name:
                out.append("ycwb.com")
            elif "越秀" in name:
                out.append("yuexiu.gov.cn")
            else:
                # 放进默认池
                out += DEFAULT_SITES
    # 去重 + 扩域
    out = list({d.strip().lower() for d in out if d.strip()})
    out = widen_domains(out)
    return out

# -------------------------- HTTP 客户端 --------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def http_get(url: str, timeout: int = 15) -> str:
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if HAS_CURL:
        r = cffi_requests.get(url, headers=headers, impersonate="chrome", timeout=timeout)
        if r.status_code == 200:
            return r.text
        return ""
    else:
        if requests is None:
            return ""
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            r.encoding = r.apparent_encoding or r.encoding
            return r.text
        return ""

# -------------------------- 解析/提取 --------------------------

def parse_html(html: str):
    if HAS_LXML:
        try:
            return LH.fromstring(html)
        except Exception:
            pass
    if HAS_BS4:
        return BeautifulSoup(html, "html.parser")
    return None

def extract_title(html: str) -> str:
    if not html:
        return ""
    # og:title / meta title
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m: return html_unescape(m.group(1).strip())
    m = re.search(r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if m: return html_unescape(m.group(1).strip())
    # h1 / h2
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.I|re.S)
    if m: return html_unescape(strip_html(m.group(1)).strip())
    m = re.search(r'<h2[^>]*>(.*?)</h2>', html, re.I|re.S)
    if m: return html_unescape(strip_html(m.group(1)).strip())
    # <title>
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I|re.S)
    if m:
        t = html_unescape(strip_html(m.group(1))).strip()
        # 站点名 | 文章题 —— 去掉站点名
        if "|" in t:
            parts = [p.strip() for p in t.split("|")]
            if len(parts[-1]) >= 6:
                return parts[-1]
        return t
    return ""


def extract_excerpt(html: str, max_len: int = 160) -> str:
    # 简单去标签
    text = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.I|re.S)
    text = re.sub(r"<style.*?>.*?</style>", " ", text, flags=re.I|re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]

def extract_date_str(text: str) -> str:
    text = text.replace("年", "-").replace("月", "-").replace("日", " ")
    m = re.search(r"(20\d{2}-\d{1,2}-\d{1,2})", text)
    return m.group(1) if m else ""

def within_date_blob(text: str, start_date: str, end_date: str) -> bool:
    if not start_date or not end_date:
        return True
    d = extract_date_str(text)
    if not d:
        return True
    # 左闭右闭
    return (start_date <= d <= end_date)

# -------------------------- 频道页兜底 --------------------------

PATTERN_CHANNELS: Dict[str, List[str]] = {
    "dayoo.com": [
        "https://news.dayoo.com/guangzhou/139995.shtml",
        "https://news.dayoo.com/finance/139999.shtml",
    ],
    "gzdaily.dayoo.com": [
        "https://news.dayoo.com/guangzhou/139995.shtml",
    ],
    "southcn.com": [
        "https://www.southcn.com/node_1_2.shtml",
    ],
    "ycwb.com": [
        "https://news.ycwb.com/node_3232.htm",
    ],
    "nfnews.com": [
        "https://static.nfnews.com/content/",
    ],
    # 可继续补：gdtv.cn / gztv.com / people.com.cn 的栏目页
}

def _is_article_url(dom: str, href: str) -> bool:
    host = reg_domain(urlparse(href).hostname or "")
    if host != dom:
        return False
    pats = ARTICLE_PATTERNS.get(dom, [])
    if not pats:
        # 没有专门规则的域名：尽量挑“看起来像正文”的链接
        if re.search(r'/20\d{2}[-/]\d{2}[-/]\d{2}/', href): return True
        if re.search(r'content[_-]?\d+', href):            return True
        if re.search(r'/\d{6}/\d{2}/\d+\.s?html', href):    return True
        return False
    return any(p.search(href) for p in pats)

def pattern_fallback_scan(dom: str,
                          keywords: List[str],
                          start_date: str,
                          end_date: str,
                          fetch_html) -> List[Dict[str, Any]]:
    t0 = time.time()
    roots = PATTERN_CHANNELS.get(dom, [])
    if not roots:
        return []

    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    cand: List[str] = []
    list_pages: set[str] = set()

    def _abs(base: str, href: str) -> str:
        if href.startswith("//"): return "https:" + href
        if href.startswith("/"):  return urljoin(base, href)
        return href

    # 抓频道页
    for root in roots:
        try:
            html = fetch_html(root) or ""
        except Exception:
            continue
        for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I):
            href = _abs(root, m.group(1))
            if href in seen: continue
            seen.add(href)

            # 识别“子分页”以扩展（仅对栏目页）
            if re.search(r'(13999\d)(?:_\d+)?\.s?html$', href):  # dayoo
                list_pages.add(href)
            if re.search(r'node_\d+(?:_\d+)?\.s?html?$', href):   # southcn/ycwb 类
                list_pages.add(href)

            # 直接收正文候选
            if _is_article_url(dom, href):
                cand.append(href)
            if len(cand) >= 180:
                break

    # 抓子分页再扩容
    for lp in list(list_pages)[:4]:
        try:
            html = fetch_html(lp) or ""
        except Exception:
            continue
        for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, re.I):
            href = _abs(lp, m.group(1))
            if href in seen: continue
            seen.add(href)
            if _is_article_url(dom, href):
                cand.append(href)
            if len(cand) >= 240:
                break

    kept = 0
    drop_by_date = 0
    # 逐条进入“正文页”抽取信息
    for href in cand[:60]:
        try:
            html = fetch_html(href)
            if not html: 
                continue
            title = extract_title(html) or ""
            body  = strip_html(html)
            if not any_kw_in(title + " " + body, keywords):
                continue

            d = extract_date_fuzzy(body + " " + title, href)
            if start_date and end_date and d:
                if not (start_date <= d <= end_date):
                    drop_by_date += 1
                    continue

            results.append({
                "title": (title or "(无标题)").strip(),
                "url": href,
                "source": reg_domain(urlparse(href).hostname or ""),
                "date": d,
                "excerpt": (body[:160] if body else ""),
                "channel": "pattern",
                "predicted_label": "0",
            })
            kept += 1
            if kept >= 30:
                break
        except Exception:
            continue

    dt = int((time.time() - t0) * 1000)
    logger.info("[pattern-fallback:%s] cand=%d subpages=%d kept=%d drop_date=%d dt=%dms",
                dom, len(cand), len(list_pages), kept, drop_by_date, dt)
    return results


# -------------------------- 搜狗微信兜底 --------------------------

def sogou_weixin_search(keywords: List[str],
                        start_date: str,
                        end_date: str,
                        fetch_html) -> List[Dict[str, Any]]:
    q = " ".join(keywords)[:60]
    if not q:
        return []
    url = f"https://weixin.sogou.com/weixin?type=2&query={quote(q)}"
    try:
        html = fetch_html(url) or ""
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for m in re.finditer(r'<a[^>]+href="(https?://mp\.weixin\.qq\.com/[^"]+)"[^>]*>(.*?)</a>', html, re.I|re.S):
        link, text = m.group(1), m.group(2)
        title = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", text)).strip()
        out.append({
            "title": title or "(无标题)",
            "url": link,
            "source": "mp.weixin.qq.com",
            "date": "",
            "channel": "wechat-sogou",
            "predicted_label": "0",
        })
        if len(out) >= 30:
            break
    return out

# -------------------------- DuckDuckGo 聚合搜索 --------------------------

def meta_search_links(keywords: List[str], domains: List[str]) -> List[str]:
    if not HAS_DDG:
        return []
    q = " ".join(keywords)
    if not q:
        return []
    out = []
    try:
        dd = DDGS()
        for hit in dd.text(q, max_results=25):
            link = hit.get("href") or ""
            host = reg_domain(urlparse(link).hostname or "")
            if link and host in set(domains):
                out.append(link)
    except Exception:
        pass
    return out

# -------------------------- Playwright JS 渲染（可选） --------------------------

def browser_fetch_html(urls: List[str]) -> List[str]:
    """用浏览器渲染拿 HTML；没装 playwright 时返回空列表。"""
    if not HAS_PW:
        return []
    html_list: List[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, viewport={"width":1280, "height":800})
            page = ctx.new_page()
            for u in urls:
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(800)
                    html_list.append(page.content())
                except Exception:
                    html_list.append("")
            browser.close()
    except Exception as e:
        logger.exception("browser_fetch_html error: %s", e)
    return html_list

# -------------------------- 爬取流程 --------------------------

def run_boost(keywords: List[str], domains: List[str],
              start_date: str, end_date: str,
              allow_wechat: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    t0 = time.time()
    collected: List[Dict[str, Any]] = []

    # 1) DuckDuckGo 聚合（装不上包就自然为 0）
    links = meta_search_links(keywords, domains)
    for link in links:
        host = reg_domain(urlparse(link).hostname or "")
        collected.append({
            "title": "",
            "url": link,
            "source": host,
            "date": "",
            "channel": "search",
            "predicted_label": "0",
        })
    logger.info("[boost] meta=%d", len(links))

    # 2) 搜狗微信（开启才跑）
    if allow_wechat:
        wx = sogou_weixin_search(keywords, start_date, end_date, http_get)
        collected += wx
        logger.info("[boost] wechat-sogou=%d", len(wx))

    # 3) 频道兜底：**一定会跑**（不是“0 才跑”）
    kept_total = 0
    for d in domains:
        try:
            got = pattern_fallback_scan(d, keywords, start_date, end_date, http_get)
            kept_total += len(got)
            collected += got
        except Exception as e:
            logger.exception("[pattern-fallback] %s error: %s", d, e)
    logger.info("[boost] pattern-kept=%d", kept_total)

    # 4) 轻补详情（少量）
    for a in collected[:12]:
        u = a.get("url") or ""
        if not u:
            continue
        try:
            html = http_get(u)
            if not html:
                continue
            if not a.get("title"):
                a["title"] = extract_title(html) or a["title"] or "(无标题)"
            if not a.get("excerpt"):
                a["excerpt"] = extract_excerpt(html)
            if not a.get("date"):
                a["date"] = extract_date_fuzzy(html, u) or ""
        except Exception:
            pass

    dt = int((time.time() - t0) * 1000)
    logger.info("[boost] total=%d dt=%dms", len(collected), dt)
    return collected, {}


def run_browser(keywords: List[str], domains: List[str],
                start_date: str, end_date: str,
                allow_wechat: bool) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not HAS_PW:
        items, _ = run_boost(keywords, domains, start_date, end_date, allow_wechat)
        return items, {"note": "playwright 未安装，已自动降级为加强模式"}

    # 有 PW：先把频道页渲染出来，再按“兜底-二次匹配”的思路筛
    urls = []
    for d in domains:
        urls += PATTERN_CHANNELS.get(d, [])
    if not urls:
        return run_boost(keywords, domains, start_date, end_date, allow_wechat)

    html_list: Dict[str, str] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, viewport={"width":1280,"height":800})
            page = ctx.new_page()
            for u in urls:
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(800)
                    html_list[u] = page.content()
                except Exception:
                    html_list[u] = ""
            browser.close()
    except Exception as e:
        logger.exception("browser_fetch_html error: %s", e)
        return run_boost(keywords, domains, start_date, end_date, allow_wechat)

    # 用渲染后的频道页做候选，再进正文判断
    items: List[Dict[str, Any]] = []
    for d in domains:
        roots = PATTERN_CHANNELS.get(d, [])
        if not roots:
            continue
        # 借用新版兜底逻辑，但把 fetch_html 定为从缓存或网络取
        def _fetch(url: str) -> str:
            if url in roots and url in html_list:
                return html_list[url]
            return http_get(url)
        items += pattern_fallback_scan(d, keywords, start_date, end_date, _fetch)

    # 轻量补充
    for a in items[:10]:
        u = a.get("url") or ""
        try:
            html = http_get(u)
            if html:
                a.setdefault("title", extract_title(html) or "(无标题)")
                a.setdefault("excerpt", extract_excerpt(html))
                a.setdefault("date", extract_date_str(html) or "")
        except Exception:
            pass

    logger.info("[browser] total=%d", len(items))
    return items, {}


# -------------------------- Flask 路由 --------------------------

def _frontend_index_path() -> str:
    # 优先环境变量
    fe_dir = os.environ.get("CRAWLER_FE_DIR")
    if fe_dir and os.path.exists(fe_dir):
        idx = os.path.join(fe_dir, "index.html")
        if os.path.exists(idx):
            return idx
    # 其次项目默认
    idx = os.path.join(PROJ_ROOT, "frontend", "index.html")
    return idx

def _frontend_base_dir() -> str:
    fe_dir = os.environ.get("CRAWLER_FE_DIR")
    if fe_dir and os.path.exists(fe_dir):
        return fe_dir
    return os.path.join(PROJ_ROOT, "frontend")

def _serve_frontend_file(relpath: str):
    """从前端目录返回指定文件，404 时给出清晰提示"""
    fe_dir = _frontend_base_dir()
    full = os.path.join(fe_dir, relpath)
    if os.path.exists(full):
        return send_from_directory(fe_dir, relpath)
    return (f"Not found: {relpath} (frontend dir = {fe_dir})", 404)

@app.get("/style.css")
def fe_style_css():
    return _serve_frontend_file("style.css")

@app.get("/app.js")
def fe_app_js():
    return _serve_frontend_file("app.js")

@app.get("/favicon.ico")
def fe_favicon():
    # 可选：如果有图标文件就返回；没有就 204
    fe_dir = _frontend_base_dir()
    ico = os.path.join(fe_dir, "favicon.ico")
    if os.path.exists(ico):
        return send_from_directory(fe_dir, "favicon.ico")
    return ("", 204)
@app.get("/")
def home():
    idx = _frontend_index_path()
    logger.info("Serving frontend from: %s", idx)
    if os.path.exists(idx):
        with open(idx, "rb") as f:
            content = f.read()
        return Response(content, mimetype="text/html; charset=utf-8")
    return "<h3>Frontend not bundled. Put your frontend/index.html, or set CRAWLER_FE_DIR.</h3>"

@app.post("/crawl")
def crawl_post():
    data = request.get_json(silent=True) or {}
    keywords = to_list(data.get("keywords"))
    media_names = to_list(data.get("media_names"))
    start_date = (data.get("start_date") or "").replace("/", "-")
    end_date = (data.get("end_date") or "").replace("/", "-")
    allow_wechat = bool(int(data.get("allow_wechat") or 0))
    mode = (data.get("mode") or "normal").strip()
    use_advanced = bool(data.get("use_advanced"))  # 兼容前端勾选

    domains = resolve_media_to_domains(media_names)
    logger.info("[resolver] media=%s -> domains=%s", ",".join(media_names) if media_names else "", ",".join(domains))
    logger.info("[crawl] mode=%s kw=%s media=%s sd=%s ed=%s adv=%s wechat=%s",
                mode, "、".join(keywords), "、".join(media_names), start_date, end_date, use_advanced, allow_wechat)

    run_id = uuid.uuid4().hex
    items: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}

    try:
        if mode == "browser":
            items, meta = run_browser(keywords, domains, start_date, end_date, allow_wechat)
        elif mode == "boost":
            items, meta = run_boost(keywords, domains, start_date, end_date, allow_wechat)
        else:
            # 普通：其实走一个“保守版加强”
            items, meta = run_boost(keywords, domains, start_date, end_date, allow_wechat)
    except Exception as e:
        logger.exception("crawl error: %s", e)
        return jsonify({"error": str(e)}), 500

    # 结果基本规整
    for a in items:
        a.setdefault("title", "(无标题)")
        a.setdefault("url", "")
        a.setdefault("source", reg_domain(urlparse(a.get("url","")).hostname or ""))
        a.setdefault("date", "")
        a.setdefault("excerpt", a.get("excerpt",""))
        a.setdefault("predicted_label", "0")

    return jsonify({
        "run_id": run_id,
        "mode": mode,
        "note": meta.get("note"),
        "count": len(items),
        "items": items,
    })

@app.post("/review")
def submit_review():
    data = request.get_json(silent=True) or {}
    run_id = data.get("run_id") or uuid.uuid4().hex
    items = data.get("items") or []
    ensure_db()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    now = int(time.time())

    saved = 0
    rows: List[List[str]] = [["title","url","source","date","predicted_label","human_label"]]
    for it in items:
        title = str(it.get("title",""))
        url = str(it.get("url",""))
        source = str(it.get("source",""))
        date = str(it.get("date",""))
        pred = str(it.get("predicted_label",""))
        human = str(it.get("human_label",""))
        if not url:
            continue
        rows.append([title,url,source,date,pred,human])
        cur.execute("INSERT INTO reviews(run_id,title,url,source,date,predicted_label,human_label,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, title, url, source, date, pred, human, now))
        saved += 1
    con.commit(); con.close()

    # 导出 CSV
    csv_name = f"review_{run_id}.csv"
    csv_path = os.path.join(EXPORT_DIR, csv_name)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)

    csv_url = f"/download/exports/{csv_name}"
    return jsonify({"saved": saved, "csv_url": csv_url})

@app.post("/export/xlsx_template")
def export_xlsx_template():
    data = request.get_json(silent=True) or {}
    proj = (data.get("project_name") or "项目").strip()
    items = data.get("items") or []

    # 目标列（参考你给的示例）：
    # A:名称  B:序号  C:新闻标题  D:报道媒体  E:刊登平台  F:媒体链接  G:备注
    rows = [["名称","序号","新闻标题","报道媒体","刊登平台","媒体链接","备注"]]
    for i, a in enumerate(items, 1):
        rows.append([
            proj,
            i,
            str(a.get("title","")),
            str(a.get("source","")),
            "",  # 刊登平台（留空给 HR 补）
            str(a.get("url","")),
            ""
        ])

    fname_noext = f"宣传模板_{proj}_{int(time.time())}"
    if HAS_OX:
        xlsx = os.path.join(EXPORT_DIR, f"{fname_noext}.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        for r in rows:
            ws.append(r)
        # 简单样式
        for col in "ABCDEFG":
            ws[f"{col}1"].font = Font(bold=True)
            ws.column_dimensions[col].width = 22
        wb.save(xlsx)
        return jsonify({"xlsx_url": f"/download/exports/{os.path.basename(xlsx)}"})
    else:
        # 回退 CSV
        csv_path = os.path.join(EXPORT_DIR, f"{fname_noext}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f); w.writerows(rows)
        return jsonify({"xlsx_url": f"/download/exports/{os.path.basename(csv_path)}"})

@app.get("/download/<path:subpath>")
def download_any(subpath: str):
    # /download/exports/xxx.csv
    # 仅开放 exports 目录
    safe_root = EXPORT_DIR
    rel = subpath.replace("\\", "/")
    parts = rel.split("/", 1)
    if not parts or parts[0] != "exports":
        return "Not allowed", 403
    if len(parts) == 1:
        return "Not found", 404
    filename = parts[1]
    return send_from_directory(safe_root, filename, as_attachment=True)

# -------------------------- 主程 --------------------------

if __name__ == "__main__":
    # 兼容 run_server.py 的调用方式
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
