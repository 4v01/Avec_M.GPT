# -*- coding: utf-8 -*-
"""
sfccn_search.py
- 站点：南方财经网（sfccn）
- 渠道：移动端 m.sfccn.com + PC 端 www.sfccn.com
- 协议：JSONP
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import scrapy


def _now_epoch_sec() -> int:
    return int(time.time())


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _strip_jsonp(body: str):
    if not body:
        return None
    m = re.search(r'\((.*)\)\s*$', body.strip(), flags=re.S)
    if not m:
        m = re.search(r'([{\[].*[}\]])', body, flags=re.S)
        if not m:
            return None
    txt = m.group(1)
    try:
        return json.loads(txt)
    except Exception:
        return None


def _clean_html(s: str) -> str:
    if not s:
        return s
    s = re.sub(r'<[^>]+>', '', s)
    return s.strip()


class SFCcnSearchSpider(scrapy.Spider):
    name = "sfccn_search"
    custom_settings = {
        "COOKIES_ENABLED": True,
        "DOWNLOAD_DELAY": 0.35,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 4.0,
    }

    def __init__(
        self,
        keywords: str = "",
        last_hours: int = 0,
        max_pages: int = 1,
        enable_mobile: int = 1,
        enable_pc: int = 1,
        dump_debug: int = 0,
        *args,
        **kwargs,
    ):
        # 彻底避免遮蔽 async start()
        # 兼容误传：start/end 与 新参数：date_start/date_end —— 全部吃掉
        self.start_raw = (kwargs.pop("date_start", None) or kwargs.pop("start", None) or None)
        self.end_raw   = (kwargs.pop("date_end", None) or kwargs.pop("end", None) or None)
        super().__init__(*args, **kwargs)

        self.kws = [k.strip() for k in keywords.split() if k.strip()] or []
        self.last_hours = int(last_hours or 0)
        self.max_pages = max(1, int(max_pages or 1))
        self.enable_mobile = int(enable_mobile or 0) == 1
        self.enable_pc = int(enable_pc or 0) == 1

        self.dump_debug = int(dump_debug or 0) == 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.debug_dir = os.path.join("debug", "sfccn", ts)

        self.UA_M = (
            "Mozilla/5.0 (Linux; Android 12; Pixel 5) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        )
        self.UA_PC = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

        self.hdr_html_m = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                           "Accept-Language": "zh-CN,zh;q=0.9",
                           "User-Agent": self.UA_M}
        self.hdr_api_m = {"Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9", "User-Agent": self.UA_M}

        self.hdr_html_pc = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "zh-CN,zh;q=0.9",
                            "User-Agent": self.UA_PC}
        self.hdr_api_pc = {"Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9", "User-Agent": self.UA_PC}

        if self.last_hours and self.last_hours > 0:
            now = datetime.now(timezone.utc)
            start = now.timestamp() - self.last_hours * 3600
            self.logger.info(
                f"sfccn_search init: kw={self.kws}, hours={self.last_hours}, "
                f"range={datetime.fromtimestamp(start).strftime('%Y-%m-%d')}~"
                f"{now.strftime('%Y-%m-%d')}"
            )
        else:
            self.logger.info(
                f"sfccn_search init: kw={self.kws}, hours=0, range>~已关闭时间过滤"
            )

    async def start(self):
        async for req in super().start():
            yield req

    def start_requests(self):
        if not self.kws:
            self.logger.warning("未提供关键词（keywords），直接退出")
            return
        for kw in self.kws:
            if self.enable_mobile:
                yield from self._seed_mobile(kw)
            if self.enable_pc:
                yield from self._seed_pc(kw)

    # ---------- 移动端 ----------
    def _seed_mobile(self, kw: str):
        kw_enc = quote_plus(kw)
        url = f"https://m.sfccn.com/channel/search/?keyword={kw_enc}"
        meta = {"cookiejar": f"m:{kw}", "kw": kw, "channel": "m", "page": 1}
        yield scrapy.Request(
            url=url,
            headers=self.hdr_html_m,
            meta=meta,
            callback=self.parse_mobile_searchpage,
            dont_filter=True,
        )

    def parse_mobile_searchpage(self, response: scrapy.http.Response):
        kw = response.meta["kw"]
        channel = response.meta["channel"]
        cookiejar = response.meta["cookiejar"]

        if self.dump_debug:
            path = os.path.join(self.debug_dir, f"m_searchpage_s{response.status}.html")
            _ensure_dir(path)
            with open(path, "wb") as f:
                f.write(response.body)
            self.logger.info(f"[DEBUG] saved {path} ({len(response.body)} bytes)")

        html = response.text or ""
        m = re.search(r'name="search_token"\s+value="([0-9a-f]{32})"', html)
        token = m.group(1) if m else "dummy"

        ref = response.url
        if "search_token=" not in ref:
            sep = "&" if "?" in ref else "?"
            ref = f"{ref}{sep}search_token={token}"

        cb = f"cb{_now_epoch_ms()}"
        hit_url = f"https://sp.sfccn.com/sfccn/search/getHit?callback={cb}"
        yield scrapy.Request(
            url=hit_url,
            headers={**self.hdr_api_m, "Referer": ref},
            meta={"cookiejar": cookiejar, "kw": kw, "ref": ref, "channel": channel},
            callback=self.parse_mobile_hit,
            dont_filter=True,
        )

    def parse_mobile_hit(self, response: scrapy.http.Response):
        if self.dump_debug:
            path = os.path.join(self.debug_dir, f"m_getHit_s{response.status}.html")
            _ensure_dir(path)
            with open(path, "wb") as f:
                f.write(response.body)
            self.logger.info(f"[DEBUG] saved {path} ({len(response.body)} bytes)")

        kw = response.meta["kw"]
        ref = response.meta["ref"]
        cookiejar = response.meta["cookiejar"]
        channel = response.meta["channel"]

        for page in range(1, self.max_pages + 1):
            cb = f"cb{_now_epoch_ms()}"
            t = _now_epoch_sec()
            kw_enc = quote_plus(kw)
            api_url = (
                f"https://sp.sfccn.com/sfccn/search/sfccn?"
                f"page={page}&keyword={kw_enc}&callback={cb}&t={t}"
            )
            yield scrapy.Request(
                url=api_url,
                headers={**self.hdr_api_m, "Referer": ref},
                meta={"cookiejar": cookiejar, "kw": kw, "channel": channel, "page": page},
                callback=self.parse_jsonp_list,
                dont_filter=True,
            )

    # ---------- PC 端 ----------
    def _seed_pc(self, kw: str):
        kw_enc = quote_plus(kw)
        url = f"https://www.sfccn.com/channel/search/?keyword={kw_enc}"
        meta = {"cookiejar": f"pc:{kw}", "kw": kw, "channel": "pc", "page": 1}
        yield scrapy.Request(
            url=url,
            headers=self.hdr_html_pc,
            meta=meta,
            callback=self.parse_pc_searchpage,
            dont_filter=True,
        )

    def parse_pc_searchpage(self, response: scrapy.http.Response):
        kw = response.meta["kw"]
        channel = response.meta["channel"]
        cookiejar = response.meta["cookiejar"]

        if self.dump_debug:
            path = os.path.join(self.debug_dir, f"pc_step1_landing_s{response.status}.html")
            _ensure_dir(path)
            with open(path, "wb") as f:
                f.write(response.body)
            self.logger.info(f"[DEBUG] saved {path} ({len(response.body)} bytes)")

        html = response.text or ""
        m = re.search(r'name="search_token"\s+value="([0-9a-f]{32})"', html)
        token = m.group(1) if m else "dummy"

        ref = response.url
        if "search_token=" not in ref:
            sep = "&" if "?" in ref else "?"
            ref = f"{ref}{sep}search_token={token}"

        cb = f"cb{_now_epoch_ms()}"
        hit_url = f"https://sp.sfccn.com/sfccn/search/getHit?callback={cb}"
        yield scrapy.Request(
            url=hit_url,
            headers={**self.hdr_api_pc, "Referer": ref},
            meta={"cookiejar": cookiejar, "kw": kw, "ref": ref, "channel": channel},
            callback=self.parse_pc_hit,
            dont_filter=True,
        )

    def parse_pc_hit(self, response: scrapy.http.Response):
        if self.dump_debug:
            path = os.path.join(self.debug_dir, f"pc_step_checkToken_s{response.status}.html")
            _ensure_dir(path)
            with open(path, "wb") as f:
                f.write(response.body)
            self.logger.info(f"[DEBUG] saved {path} ({len(response.body)} bytes)")

        kw = response.meta["kw"]
        ref = response.meta["ref"]
        cookiejar = response.meta["cookiejar"]
        channel = response.meta["channel"]

        for page in range(1, self.max_pages + 1):
            cb = f"cb{_now_epoch_ms()}"
            t = _now_epoch_sec()
            kw_enc = quote_plus(kw)
            api_url = (
                f"https://sp.sfccn.com/sfccn/search/sfccn?"
                f"page={page}&keyword={kw_enc}&callback={cb}&t={t}"
            )
            yield scrapy.Request(
                url=api_url,
                headers={**self.hdr_api_pc, "Referer": ref},
                meta={"cookiejar": cookiejar, "kw": kw, "channel": channel, "page": page},
                callback=self.parse_jsonp_list,
                dont_filter=True,
            )

    # ---------- 列表 JSONP 统一解析 ----------
    def parse_jsonp_list(self, response: scrapy.http.Response):
        channel = response.meta["channel"]
        kw = response.meta["kw"]
        page = int(response.meta.get("page", 1))

        if self.dump_debug:
            prefix = "api_m" if channel == "m" else "api_pc"
            path = os.path.join(self.debug_dir, f"{prefix}_p{page}_s{response.status}.html")
            _ensure_dir(path)
            with open(path, "wb") as f:
                f.write(response.body)
            self.logger.info(f"[DEBUG] saved {path} ({len(response.body)} bytes)")

        obj = _strip_jsonp(response.text or "")
        items = []
        if isinstance(obj, dict):
            status = obj.get("status")
            if status != 1:
                self.logger.warning(
                    f"[{channel.upper()}][API_STATUS] page={page} status={status} body={response.text[:60]}..."
                )
                return
            data = obj.get("data")
            if isinstance(data, list):
                items = data
        elif isinstance(obj, list):
            items = obj
        else:
            self.logger.warning(
                f"[{channel.upper()}][API_PARSE] page={page} 非法 JSONP：{(response.text or '')[:60]}..."
            )
            return

        self.logger.info(f"[{channel.upper()}][PAGE {page}] candidates={len(items)}")

        for it in items:
            if not isinstance(it, dict):
                continue
            title_raw = (it.get("title") or it.get("bt") or "").strip()
            title = _clean_html(title_raw)

            url = (it.get("url") or it.get("linkurl") or "").strip()
            if not url:
                continue

            source = (it.get("source") or it.get("author") or "南方财经").strip()
            ts = it.get("inputtime") or it.get("updatetime") or it.get("publishtime")
            if isinstance(ts, (int, float)) and ts > 0:
                dt = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
            else:
                dt = ""

            yield {
                "title": title,
                "title_raw": title_raw,
                "url": url,
                "summary": _clean_html(it.get("description") or ""),
                "source": source,
                "date": dt,
                "site": "sfccn",
                "keywords": ", ".join(self.kws),
                "channel": channel,
            }
