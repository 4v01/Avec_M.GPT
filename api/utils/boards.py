# api/utils/boards.py
# 统一解析“板块配置”，兼容：
# - str:   紧凑语法（多行） 例：工程服务: 智能建造; 装配式 | spiders=dayoo_search,ycwb_search
# - list[dict]: [{"name": "...", "keywords": [...], "spiders": [...]}, ...]
# - dict:  {"工程服务": {"keywords": [...], "spiders": [...]}, ...} 或 {"boards":[...]}
from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple

BoardMap = Dict[str, List[str]]

DEFAULT_BOARDS: BoardMap = {
    "头条": ["政策","通知","意见","指导意见","实施意见","行动计划","若干措施","征求意见稿","发布会","权威解读","标准","规范","指南","办法","细则","技术导则","国家发展改革委","住建部","工信部","财政部","自然资源部","国务院国资委","省国资委","广东省","广州市","深圳市","重大项目","重大投资","签约","落地","揭牌","授牌","开工","投产","竣工","通车","封顶","领导","调研","督导","座谈","指示"],
    "城市更新与房地产": ["城市更新条例","更新单元","连片改造","旧城","旧村","旧厂","城中村改造","工改工","工改商","工改居","规划公示","批前公示","控规","详规","更新实施方案","年度计划","保障性住房","保障性租赁住房","集体土地","租购并举","回迁","安置房","产业园","产业空间","园区改造","城市更新项目","房地产市场","土地出让","土拍","招挂复合","集中供地"],
    "工程服务": ["智能建造","装配式","装配式装修","工业化建筑","PC构件","钢结构","CIM","BIM","数字孪生","全过程咨询","EPC","总承包","代建","监理","招标公告","资格预审","中标候选人","中标结果","安全生产通报","质量安全","工期","文明施工","扬尘"],
    "城市运营": ["文体场馆","体育中心","会展","综合体","文旅运营","商业运营","商业管理","TOD","轨道上盖","物业管理","城市运营公司","存量资产运营","养老","康养","医养结合","社区养老","长者服务"],
    "集团动态": ["珠江实业","珠江实业集团","珠江城投","珠江商管","珠江监理","珠江装配","珠江设计","珠江城更","签约","中标","授牌","表彰","奖项","开工","投产","竣工","中标候选人"],
    "资产盘活": ["盘活存量资产","低效用地盘活","不动产盘活","腾笼换鸟","盘活行动","基础设施REITs","不动产REITs","公募REITs","资产证券化","REITs项目","闲置资产","低效资产","存量更新"],
    "新规速递": ["印发","出台","实施细则","暂行办法","管理办法","征求意见","权威解读","典型案例","建筑","城市更新","房地产","园区","资产管理","物业","养老","体育","会展","低空","数据要素"],
    "国企改革": ["国企改革三年行动","改革深化提升行动","双百行动","科改示范","对标世界一流","授权放权","经理层任期制","契约化管理","混合所有制改革","股权多元化","省国资委","市国资委","国资监管","绩效考核"],
    "百千万工程": ["百千万工程","县域经济","强县促镇带村","强县工程","镇村融合","和美乡村","环南昆山","罗浮山","增城","引领建设区","绿美广东","绿美广州"],
    "数智化": ["数智化","数字化转型","新质生产力","AIGC","大模型","智能体","知识库","OCR","RPA","数据要素","数据二十条","数据流通","算力","智算中心","城市大脑","数据治理","数据安全","CIM","BIM","三维实景","时空信息","物联网平台"],
    "低空经济": ["低空经济","通用航空","通航","eVTOL","无人机","直升机场","起降点","空域改革","飞行服务站","低空航线","低空产业园","低空示范区","低空试点"],
    "他山之石": ["中建","中铁","中交","中冶","中电建","中能建","中材","中航","中车","中海油","中石化","中石油","华润","招商蛇口","保利发展","中国金茂","中海地产","万科","绿城","龙湖","越秀地产","金地","示范","样板","做法","经验","对标","复制","中标","中选","落地"]
  }

DEFAULT_SPIDERS = [
    "dayoo_search", "ycwb_search",
    "ifeng_search", "gov_search", "nfnews_search"
]

def _dedup(seq: List[str]) -> List[str]:
    seen = {}
    for x in (seq or []):
        x = (x or "").strip()
        if x:
            seen.setdefault(x, True)
    return list(seen.keys())

def split_keywords(s: str) -> List[str]:
    if not s:
        return []
    # 支持；;、,，/| 和空白分隔
    parts = re.split(r"[;；、,/|，\s]+", s.strip())
    return _dedup([p for p in parts if p])

def ensure_board_obj(d: Dict[str, Any]) -> Dict[str, Any]:
    # 允许 name/title/board 任一作为名称字段
    name = (d.get("name") or d.get("title") or d.get("board") or "").strip()
    if not name:
        raise ValueError("board object missing name/title")
    kws = d.get("keywords")
    if isinstance(kws, str):
        keywords = split_keywords(kws)
    elif isinstance(kws, list):
        keywords = _dedup([str(x) for x in kws])
    else:
        keywords = []
    sp = d.get("spiders")
    if isinstance(sp, str):
        spiders = _dedup([x for x in re.split(r"[,\s]+", sp.strip()) if x])
    elif isinstance(sp, list):
        spiders = _dedup([str(x) for x in sp])
    else:
        spiders = DEFAULT_SPIDERS
    return {"name": name, "keywords": keywords, "spiders": spiders}

def to_mapping(boards: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[str]]]:
    out: Dict[str, Dict[str, List[str]]] = {}
    for b in boards:
        b = ensure_board_obj(b)
        out[b["name"]] = {"keywords": b["keywords"], "spiders": b["spiders"]}
    return out

def parse_boards_compact(text: str) -> Dict[str, Dict[str, List[str]]]:
    """
    紧凑语法（每行一个板块）：
      工程服务: 智能建造; 装配式; EPC | spiders=dayoo_search,ycwb_search
      城市运营: 文体场馆; TOD; 物业管理
    """
    if not isinstance(text, str):
        raise TypeError("parse_boards_compact expects str")
    out: Dict[str, Dict[str, List[str]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 允许后缀指定 spiders=...
        spiders: List[str] = []
        m = re.search(r"\|\s*spiders\s*=\s*([A-Za-z0-9_, -]+)$", line)
        if m:
            spiders = _dedup([x for x in re.split(r"[,\s]+", m.group(1).strip()) if x])
            line = line[:m.start()].rstrip()
        # 形如 "板块名: 关键词..."；冒号可省略则默认整行是关键词（但建议带板块名）
        if ":" in line:
            name, kw = line.split(":", 1)
            name = name.strip()
            keywords = split_keywords(kw)
        else:
            # 没写名称就跳过，避免出现空名 sheet
            continue
        if not spiders:
            spiders = DEFAULT_SPIDERS
        out[name] = {"keywords": keywords, "spiders": spiders}
    return out

def parse_boards_any(data: Any, defaults: Dict[str, Dict[str, List[str]]] | None = None
                     ) -> Dict[str, Dict[str, List[str]]]:
    """
    入口：自动识别数据结构并转换为 {name: {keywords:[], spiders:[]}}
    """
    if data is None:
        return defaults or {}

    # {"boards":[{...}, ...]}
    if isinstance(data, dict) and "boards" in data and isinstance(data["boards"], list):
        return to_mapping(data["boards"])

    # {"工程服务": {...}, "城市运营": {...}} 或 {"工程服务": ["kw1","kw2"], ...}
    if isinstance(data, dict):
        out: Dict[str, Dict[str, List[str]]] = {}
        for name, val in data.items():
            if name == "boards":
                continue
            if isinstance(val, dict):
                kws = val.get("keywords")
                kw_list = split_keywords(kws) if isinstance(kws, str) else _dedup(list(kws or []))
                sp = val.get("spiders")
                sp_list = _dedup([x for x in re.split(r"[,\s]+", sp.strip()) if x]) if isinstance(sp, str) \
                          else _dedup(list(sp or [])) if isinstance(sp, list) else DEFAULT_SPIDERS
                out[str(name).strip()] = {"keywords": kw_list, "spiders": sp_list}
            elif isinstance(val, list):
                out[str(name).strip()] = {"keywords": _dedup([str(x) for x in val]), "spiders": DEFAULT_SPIDERS}
            elif isinstance(val, str):
                out[str(name).strip()] = {"keywords": split_keywords(val), "spiders": DEFAULT_SPIDERS}
        return out if out else (defaults or {})

    # list[dict] 形式
    if isinstance(data, list):
        if all(isinstance(x, dict) for x in data):
            return to_mapping(data)  # [{"name":...}]
        # list[str] → 紧凑文本合并解析
        strs = [x for x in data if isinstance(x, str)]
        if strs:
            return parse_boards_compact("\n".join(strs))
        return defaults or {}

    # 紧凑文本
    if isinstance(data, str):
        return parse_boards_compact(data)

    return defaults or {}
