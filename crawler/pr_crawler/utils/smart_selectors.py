# -*- coding: utf-8 -*-
"""
智能结果块发现（无学习版）
- 在站点结构改动时，作为 spider 的兜底：自动在 DOM 中找“像新闻条目的块”
- 基于启发式打分：链接+标题文本长度、是否包含日期/来源、类名关键词、结构密度等
"""
from __future__ import annotations
import re
from typing import List, Tuple, Dict, Optional

from parsel import Selector

DATE_RE = re.compile(
    r"(?:20\d{2}|19\d{2})[./\-年]\s*\d{1,2}[./\-月]\s*\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2})?",
    re.I
)
SOURCE_HINT_RE = re.compile(r"(来源|出处|作者|记者|编辑)\s*[:：]?\s*[\u4e00-\u9fa5A-Za-z0-9_-]{2,}", re.I)
CLASS_HINT_RE = re.compile(r"(res|result|list|news|item|article|search)", re.I)

def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text))
    return text.replace("\u00A0", " ").replace("&nbsp;", " ").strip()

def _text_len_score(t: str) -> float:
    n = len(t)
    # 过短/过长都扣分，8~80 长度段给高分
    if n < 6: return 0.0
    if n > 140: return 0.2
    if 8 <= n <= 80: return 1.0
    if 80 < n <= 120: return 0.7
    return 0.4

def _class_tokens_score(classes: str) -> float:
    if not classes:
        return 0.0
    hits = CLASS_HINT_RE.findall(classes)
    return min(1.0, 0.25 * len(hits)) if hits else 0.0

def _date_present_score(html: str) -> float:
    return 0.6 if DATE_RE.search(html) else 0.0

def _source_present_score(html: str) -> float:
    return 0.3 if SOURCE_HINT_RE.search(html) else 0.0

def _candidate_blocks(sel: Selector):
    # 常见承载元素
    return sel.css("li, div, article, section")

def guess_result_nodes(sel: Selector, topk: int = 30) -> List[Tuple[float, Selector]]:
    """
    返回 [(score, node_selector), ...]，score 高者更像“新闻条目”
    """
    out: List[Tuple[float, Selector]] = []
    for blk in _candidate_blocks(sel):
        # 标题候选：块内第一条 <a> 文本（或 h1/h2/h3 内 a）
        a = blk.css("h1 a, h2 a, h3 a, a").xpath("string(.)").get()
        href = blk.css("h1 a::attr(href), h2 a::attr(href), h3 a::attr(href), a::attr(href)").get()
        title = _clean(a)
        if not href or not title:
            continue

        # 评分
        sc = 0.0
        sc += _text_len_score(title)                        # 文本长度/可读性
        sc += _class_tokens_score(_clean(blk.attrib.get("class", "")))  # 类名命中
        html = blk.get() or ""
        sc += _date_present_score(html)                     # 附近是否有日期
        sc += _source_present_score(html)                   # 附近是否有来源提示

        # 密度轻量加成：块中文字/标签比
        text_only = _clean(blk.xpath("string(.)").get() or "")
        tag_count = len(blk.xpath(".//*").getall()) or 1
        dens = min(1.0, len(text_only) / (tag_count * 50.0))
        sc += 0.2 * dens

        if sc >= 0.9:   # 经验阈值：太低的不要
            out.append((sc, blk))

    out.sort(key=lambda x: x[0], reverse=True)
    return out[:topk]

def extract_fields_from_block(blk: Selector) -> Dict[str, str]:
    # 题目 & 链接
    a_node = blk.css("h1 a, h2 a, h3 a, a")
    title = _clean(a_node.xpath("string(.)").get())
    url = _clean(a_node.xpath("@href").get())

    # 摘要
    summary = ""
    for css in [".res-desc", ".summary", "p", ".desc", ".abstract"]:
        summary = _clean(blk.css(css).xpath("string(.)").get())
        if len(summary) >= 10:
            break

    # 来源/时间（在块内 DOM 文本里 fuzzy 提取）
    text = _clean(blk.xpath("string(.)").get() or "")
    source = ""
    pub = ""
    m = SOURCE_HINT_RE.search(text)
    if m:
        # 取匹配后若干字做来源
        tail = text[m.start(): m.end()+10]
        source = _clean(re.sub(r".*?(来源|出处|作者|记者|编辑)\s*[:：]?\s*", "", tail))
    d = DATE_RE.search(text)
    if d:
        pub = _clean(d.group(0))

    return {"title": title, "url": url, "summary": summary, "source": source, "published_at": pub}
