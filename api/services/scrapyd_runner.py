# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

__all__ = ["list_projects", "list_spiders", "schedule_spider", "run_all"]

SCRAPYD_URL = os.environ.get("SCRAPYD_URL", "http://127.0.0.1:6800").rstrip("/")
DEFAULT_PROJECT = os.environ.get("SCRAPYD_PROJECT", "pr_crawler")

def _client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(base_url=SCRAPYD_URL, timeout=timeout)

def list_projects() -> List[str]:
    with _client() as c:
        r = c.get("/listprojects.json")
        r.raise_for_status()
        return (r.json().get("projects") or [])

def list_spiders(project: str = DEFAULT_PROJECT) -> List[str]:
    with _client() as c:
        r = c.get("/listspiders.json", params={"project": project})
        r.raise_for_status()
        return (r.json().get("spiders") or [])

def schedule_spider(
    *,
    project: str,
    spider: str,
    args: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    args = dict(args or {})
    form: Dict[str, Any] = {"project": project, "spider": spider}
    form.update(args)
    with _client() as c:
        r = c.post("/schedule.json", data=form)
        try:
            data = r.json()
        except Exception:
            return {"status": "error", "spider": spider, "message": f"bad response: {r.text[:200]}"}
        if str(data.get("status", "")).lower() == "ok" and data.get("jobid"):
            return {"status": "ok", "spider": spider, "scrapyd_jobid": data["jobid"]}
        return {"status": "error", "spider": spider, "message": data.get("message") or r.text[:200]}

def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default

def _to_bool01(v: Any, default: int = 1) -> str:
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):  return "1"
    if s in ("0", "false", "no", "n", "off", ""): return "0"
    try:
        return "1" if int(s) != 0 else "0"
    except Exception:
        return str(default)

def _norm_keywords(kws: Any) -> str:
    if kws is None:
        return ""
    if isinstance(kws, list):
        return " ".join([str(x).strip() for x in kws if str(x).strip()])
    return str(kws).strip()

def _pick_spiders(project: str, wanted: Optional[List[str]]) -> List[str]:
    all_sp = list_spiders(project)
    if wanted:
        wl = {s.strip() for s in wanted if s and s.strip()}
        return [s for s in all_sp if s in wl]
    # 默认策略：仅挑 *_search，且排除 chinaso_search + sfccn_search
    out = [s for s in all_sp if s.endswith("_search") and s not in {"chinaso_search", "sfccn_search"}]
    return sorted(out)

def run_all(job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    统一调度所有 spider（“以爬虫实现为准”的参数名）：
      - keywords: str | List[str]
      - date_start: 'YYYY-MM-DD'  # 显式时间窗（优先）
      - date_end:   'YYYY-MM-DD'
      - last_hours: int           # 仅当未提供显式时间窗时，爬虫内部才会回退使用
      - max_pages:  int
      - enable_mobile: 0/1 or bool
      - enable_pc:     0/1 or bool
      - spiders: Optional[List[str]] 指定子集；不传则自动选择 *_search（剔除 chinaso_search）
    """
    project = payload.get("project") or DEFAULT_PROJECT

    # ---- 关键词（只保留 keywords 一个名字）----
    keywords = _norm_keywords(payload.get("keywords"))

    # ---- 时间窗：只用 date_start/date_end；不再塞入 start/end/kw/q 等旧别名 ----
    date_start = str(payload.get("date_start") or "").strip()
    date_end   = str(payload.get("date_end") or "").strip()
    last_hours = _safe_int(payload.get("last_hours", 0), 0)
    max_pages  = _safe_int(payload.get("max_pages", 1), 1)
    enable_mobile = _to_bool01(payload.get("enable_mobile", 1), 1)
    enable_pc     = _to_bool01(payload.get("enable_pc", 1), 1)

    # ---- 目标爬虫列表 ----
    spiders = payload.get("spiders")
    if isinstance(spiders, str):
        spiders = [s.strip() for s in spiders.split(",") if s.strip()]
    if not (isinstance(spiders, list) and spiders):
        spiders = _pick_spiders(project, None)

    # ---- 统一 -a 参数，仅包含爬虫真正使用的字段 ----
    args: Dict[str, Any] = {
        "job_tag": f"{job_id}",
        "keywords": keywords,
        "max_pages": max_pages,
        "enable_mobile": enable_mobile,
        "enable_pc": enable_pc,
    }
    # 显式时间窗优先；交由爬虫基类处理
    if date_start:
        args["date_start"] = date_start
    if date_end:
        args["date_end"] = date_end
    # 只有在前端确实给了 last_hours，我们才传入；否则让爬虫保持默认 0
    if last_hours > 0 and not (date_start or date_end):
        args["last_hours"] = last_hours

    results: List[Dict[str, Any]] = []
    for sp in spiders:
        r = schedule_spider(project=project, spider=sp, args=args)
        results.append(r)

    ok = [x for x in results if x.get("status") == "ok"]
    return {"status": "ok" if ok else "error", "scheduled": results, "args": args, "project": project}
