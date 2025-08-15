# -*- coding: utf-8 -*-
# src/crawler_core/utils/site_resolver.py
# Franglish comments (ASCII only). Resolve media name -> candidate domains (web + wechat).

from __future__ import annotations
import re, json
from urllib.parse import urlparse
from typing import Optional, Sequence, List, Dict
from pathlib import Path

from crawler_core.storage.sqlite_store import init_db, get_site_domain, add_site_mapping
from crawler_core.utils.search import search_multi

# Third-party hosts (we normally avoid), but we may ALLOW wechat.
_THIRD_DEFAULT = (
    "weibo.com","kuaibao.qq.com","toutiao.com","baijiahao.baidu.com","zhihu.com",
    "xhs.cn","bilibili.com","douyin.com","kuaishou.com"
)
_WECHAT_HOST = "mp.weixin.qq.com"

def _norm_host(h: str) -> str:
    h = (h or "").lower().strip()
    return re.sub(r"^(www|m|mp|wap)\.", "", h)

def _looks_like_wechat_name(name: str) -> bool:
    # heuristics: gov news accounts ending with 发布/办/台/频道/政务等
    return any(name.endswith(t) for t in ("发布","办","台","频道","时政","中心"))

# ---------- Built-in alias (receive list of domains + optional platform) ----------
# For uncertain ones we keep empty list and rely on search fallback.
_ALIAS: Dict[str, Dict] = {
    # === Invited list (excerpt) ===
    "中央广电总台": {"domains": ["cctv.com","cnr.cn","yangshipin.cn"], "platform": "web"},
    "人民日报数字广东": {"domains": ["gd.people.com.cn"], "platform": "web"},
    "央广网": {"domains": ["cnr.cn"], "platform": "web"},
    "中新社": {"domains": ["chinanews.com.cn"], "platform": "web"},
    "中国企业报": {"domains": ["zqcn.com.cn"], "platform": "web"},  # China Enterprise News
    "中国青年报": {"domains": ["cyol.com"], "platform": "web"},
    "中国报道": {"domains": ["china-report.com.cn","china-report.net"], "platform": "web"},  # fallback by search if not match
    "上海证券报": {"domains": ["cnstock.com"], "platform": "web"},
    "南方日报": {"domains": ["southcn.com"], "platform": "web"},
    "羊城晚报": {"domains": ["ycwb.com","news.ycwb.com"], "platform": "web"},
    "广州日报": {"domains": ["dayoo.com","gzdaily.dayoo.com"], "platform": "web"},
    "南方都市报": {"domains": ["nandu.com","static.nfapp.southcn.com","southcn.com"], "platform": "web"},
    "新快报": {"domains": ["xkb.com.cn"], "platform": "web"},
    "广东建设报": {"domains": ["gdjsb.net"], "platform": "web"},  # adjust if you know exact
    "信息时报": {"domains": ["xxsb.com","gzxxts.com"], "platform": "web"},
    "广东电视台": {"domains": ["gdtv.cn"], "platform": "web"},
    "广东经视": {"domains": ["gdtv.cn"], "platform": "web"},
    "大湾区卫视": {"domains": ["gdtv.cn"], "platform": "web"},
    "广东民生DV现场": {"domains": ["gdtv.cn"], "platform": "web"},
    "广东广播电视台广播融媒体中心": {"domains": ["gdtv.cn"], "platform": "web"},
    "广东台时政": {"domains": ["gdtv.cn"], "platform": "web"},
    "广州电视台": {"domains": ["gztv.com"], "platform": "web"},   # fallback-search if needed
    "广州电台": {"domains": [], "platform": "web"},                # let search decide
    "广州交通台": {"domains": [], "platform": "web"},              # let search decide
    "大洋网": {"domains": ["dayoo.com"], "platform": "web"},

    # === SouthCN family ===
    "南方网": {"domains": ["southcn.com"], "platform": "web"},
    "南方+": {"domains": ["nfnews.com","static.nfnews.com","pc.nfapp.southcn.com","southcn.com"], "platform": "web"},

    # === Guangzhou districts (correct domain forms) ===
    "广州越秀发布": {"domains": ["yuexiu.gov.cn"], "platform": "web"},
    "广州荔湾发布": {"domains": ["liwan.gov.cn"], "platform": "web"},
    "广州海珠发布": {"domains": ["haizhu.gov.cn"], "platform": "web"},
    "广州天河发布": {"domains": ["tianhe.gov.cn","thnet.gov.cn"], "platform": "web"},
    "广州白云发布": {"domains": ["baiyun.gov.cn"], "platform": "web"},
    "广州黄埔发布": {"domains": ["huangpu.gov.cn","gdd.gov.cn","hp.gov.cn"], "platform": "web"},  # include dev zone
    "广州花都发布": {"domains": ["huadu.gov.cn"], "platform": "web"},
    "广州番禺发布": {"domains": ["panyu.gov.cn"], "platform": "web"},
    "广州南沙发布": {"domains": ["nansha.gov.cn"], "platform": "web"},
    "广州从化发布": {"domains": ["conghua.gov.cn"], "platform": "web"},
    "广州增城发布": {"domains": ["zengcheng.gov.cn"], "platform": "web"},

    # === General ===
    "人民网": {"domains": ["people.com.cn","cpc.people.com.cn","politics.people.com.cn"], "platform": "web"},
    "新华社": {"domains": ["xinhuanet.com","news.cn"], "platform": "web"},
    "央视网": {"domains": ["cctv.com","news.cctv.com"], "platform": "web"},
    "广州发布": {"domains": ["gz.gov.cn"], "platform": "web"}
}

def _load_external() -> Dict[str, Dict]:
    """Load extra aliases from var/aliases_extra.json (hot reload)."""
    try:
        root = Path(__file__).resolve().parents[3]
    except Exception:
        root = Path.cwd()
    for cand in (root / "var" / "aliases_extra.json", Path("var/aliases_extra.json")):
        if cand.exists():
            try:
                data = json.loads(cand.read_text("utf-8"))
                fixed: Dict[str, Dict] = {}
                for k, v in data.items():
                    if isinstance(v, dict):
                        doms = v.get("domains", [])
                        plat = v.get("platform", "web")
                    elif isinstance(v, list):
                        doms = v; plat = "web"
                    else:
                        doms = [str(v)]; plat = "web"
                    fixed[k.strip()] = {"domains": [_norm_host(x) for x in doms], "platform": plat}
                return fixed
            except Exception:
                return {}
    return {}

class SiteResolver:
    def __init__(self) -> None:
        init_db()

    def resolve_multi(self, name: str, top_k: int = 4, allow_wechat: bool = True) -> List[str]:
        """Return candidate hosts (may include mp.weixin.qq.com if allowed/needed)."""
        name = (name or "").strip()
        if not name: return []

        # Merge external aliases
        alias = dict(_ALIAS)
        alias.update(_load_external())

        out: List[str] = []

        if name in alias:
            out.extend([_norm_host(x) for x in alias[name].get("domains", [])])

        # legacy cache (first domain only)
        cached = get_site_domain(name)
        if cached:
            out.append(_norm_host(cached))

        # search fallback
        third = set(_THIRD_DEFAULT)
        wechat_ok = allow_wechat and (_looks_like_wechat_name(name) or alias.get(name, {}).get("platform") == "wechat")

        # allow wechat host if heuristics say so
        if wechat_ok:
            third.discard(_WECHAT_HOST)

        if not out:
            q = f"{name} 官网"
            urls = search_multi(q, max_results=10)
            for u in urls:
                host = _norm_host(urlparse(u).netloc)
                if not host: continue
                if any(t in host for t in third):  # still block most 3rd-party platforms
                    continue
                if host not in out:
                    out.append(host)
                if len(out) >= top_k:
                    break

        # ensure unique/cap and cache first
        out = list(dict.fromkeys(out))[:top_k]
        if out:
            add_site_mapping(name, out[0])
        return out

    # compat with old code
    def resolve(self, name: str) -> Optional[str]:
        arr = self.resolve_multi(name, top_k=1)
        return arr[0] if arr else None

    def discover_domains_by_keywords(self, keywords: Sequence[str], top_n: int = 3) -> List[str]:
        if not keywords: return []
        q = " ".join(keywords[:6])
        urls = search_multi(q, max_results=12)
        third = set(_THIRD_DEFAULT)  # we do NOT allow wechat here by default
        hosts, seen = [], set()
        for u in urls:
            host = _norm_host(urlparse(u).netloc)
            if host and host not in seen and not any(x in host for x in third):
                seen.add(host); hosts.append(host)
                if len(hosts) >= top_n: break
        return hosts
