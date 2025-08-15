from __future__ import annotations

import os, logging, uuid
from typing import Any, Dict, List, Optional
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from crawler_core.storage.sqlite_store import (
    init_db, add_training_sample, get_training_data,
    save_review_results, export_run_to_csv,
    record_ml_run, set_ml_state, get_ml_state
)
from crawler_core.scraping.manager import CrawlerManager
from crawler_core.ml.naive_bayes import NaiveBayesClassifier
from crawler_core.ml.eval import train_and_eval
from crawler_core.config import resolve_paths
from crawler_core.storage.sqlite_store import get_ml_state
from crawler_core.ml.model_selector import make_model
from crawler_core.logging_setup import setup_logging
from crawler_core.utils.search import search_multi

setup_logging()
logger = logging.getLogger(__name__)

paths = resolve_paths()
app = Flask(__name__, static_folder=None)
CORS(app)

init_db()

def _ensure_list(val: Any) -> List[str]:
    if val is None: return []
    if isinstance(val, list): return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str): return [p.strip() for p in val.split(",") if p.strip()]
    return []

def _default_text(a: Dict[str, Any]) -> str:
    return f"{a.get('title','')}\n{a.get('excerpt','')}".strip()

def _resolve_fe_dirs() -> List[str]:
    cands: List[str] = []
    if paths.frontend_dir: cands.append(paths.frontend_dir)
    cands.append(os.path.join(paths.base_dir, "frontend"))
    cands.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "frontend")))
    seen=set(); ret=[]
    for d in cands:
        if d and d not in seen:
            seen.add(d); ret.append(d)
    return ret

@app.route("/")
def _index():
    for d in _resolve_fe_dirs():
        idx = os.path.join(d, "index.html")
        if os.path.exists(idx):
            logger.info("Serving frontend from: %s", idx)
            return send_from_directory(d, "index.html")
    logger.warning("Frontend not found. Checked: %s", _resolve_fe_dirs())
    return "Frontend not bundled. Open frontend/index.html manually.", 200

@app.route("/<path:asset_path>")
def _assets(asset_path: str):
    for d in _resolve_fe_dirs():
        candidate = os.path.join(d, asset_path)
        if os.path.exists(candidate):
            return send_from_directory(d, asset_path)
    return ("Not Found", 404)

@app.route("/favicon.ico")
def _favicon():
    return ("", 204)

@app.get("/debug/search")
def debug_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "q required. example: /debug/search?q=site:dayoo.com 聚龙湾"}), 400
    urls = search_multi(q, max_results=20)
    return jsonify({"count": len(urls), "urls": urls})

@app.get("/ping")
def ping() -> Any:
    return jsonify({"status":"ok"})

# ---------- 支持 GET/POST 两种入口 ---------- #
def _crawl_route() -> Any:
    data = request.get_json(silent=True) or {}
    
    def _as_list(v):
        if v is None: return []
        if isinstance(v, (list, tuple)): return [str(x).strip() for x in v if str(x).strip()]
        return [x.strip() for x in str(v).split(",") if x.strip()]

    keywords = _as_list(data.get("keywords")) or _as_list(request.args.get("keywords", ""))
    media_names = _as_list(data.get("media_names")) or _as_list(request.args.get("media_names", ""))

    start_date = data.get("start_date") or request.args.get("start_date") or None
    end_date   = data.get("end_date")   or request.args.get("end_date")   or None

    ua = data.get("use_advanced")
    if ua is None:
        ua = str(request.args.get("use_advanced", "1")).lower() in ("1","true","yes")
    use_advanced = bool(ua)

    aw = data.get("allow_wechat")
    if aw is None:
        aw = str(request.args.get("allow_wechat", "0")).lower() in ("1","true","yes")
    allow_wechat = bool(aw)

    if not keywords:
        return jsonify({"error": "keywords required. Example: /crawl?keywords=聚龙湾,罗冲围&media_names=大洋网"}), 400

    mgr = CrawlerManager()
    try:
        articles = mgr.crawl(
            keywords=keywords,
            media_names=media_names or None,
            start_date=start_date,
            end_date=end_date,
            use_advanced=use_advanced,
            strict_date=1,
            allow_wechat=allow_wechat,
        )
    except TypeError:
        articles = mgr.crawl(keywords=keywords)

    # 轻量分类（仅作排序/标注；真正“撞库补充”由 manager 内部按开关控制）
    try:
        samples = get_training_data()
        if samples:
            clf = NaiveBayesClassifier()
            clf.train(samples)
            preds = clf.predict([_default_text(a) for a in articles])
        else:
            preds = [0]*len(articles)
    except Exception:
        preds = [0]*len(articles)

    run_id = uuid.uuid4().hex
    out = []
    for a, y in zip(articles, preds):
        b = dict(a); b["predicted_label"] = str(int(y)); out.append(b)

    # 取当前 ML 状态的模型（默认 nb）
    state = get_ml_state()
    model_name = str(state.get("model", "nb"))
    samples = get_training_data()
    if samples:
        clf = make_model(model_name)
        clf.train(samples)
        preds = clf.predict([_default_text(a) for a in articles])
    else:
        preds = [0] * len(articles)
    return jsonify({"run_id": run_id, "count": len(out), "items": out, "ml_assist": int(state.get("active",0))})

@app.post("/crawl")
def crawl_post() -> Any:
    return _crawl_route()

@app.get("/crawl")
def crawl_get() -> Any:
    return _crawl_route()

# ---------- 复验→入库→导出 ---------- #
@app.post("/review")
def review_route() -> Any:
    data = request.get_json(silent=True) or {}
    run_id: str = str(data.get("run_id") or "").strip()
    items: List[Dict[str, Any]] = data.get("items") or []
    if not run_id or not isinstance(items, list) or not items:
        return jsonify({"error":"run_id and items required"}), 400
    keywords = ",".join(_ensure_list(data.get("keywords")))
    media_names = ",".join(_ensure_list(data.get("media_names")))
    saved = save_review_results(run_id, items, keywords, media_names)
    export_dir = os.path.join(paths.var_dir or ".", "exports")
    csv_path = export_run_to_csv(run_id, export_dir)
    rel = os.path.relpath(csv_path, export_dir).replace("\\","/")
    csv_url = f"/download/exports/{rel}"
    return jsonify({"message":"ok","saved":saved,"csv_url":csv_url})

@app.get("/download/exports/<path:filename>")
def download_export(filename: str):
    export_dir = os.path.join(paths.var_dir or ".", "exports")
    return send_from_directory(export_dir, filename, as_attachment=True)

# ---------- ML：训练+评估+门槛开关 ---------- #
@app.post("/ml/train")
def ml_train_route() -> Any:
    data = request.get_json(silent=True) or {}
    model = str(data.get("model","nb")).lower()
    threshold = float(data.get("threshold", 0.7))
    samples = get_training_data()
    res = train_and_eval(model, samples, threshold=threshold)
    if not res.get("ok"):
        return jsonify({"error":"not-enough-samples","n":res.get("n",0)}), 400
    # 记录本次评估
    record_ml_run(
        model,
        float(res["precision"]),
        float(res["recall"]),
        float(res["f1"]),
        int(res["n_train"]),
        int(res["n_test"])
    )
    # 是否过阈值 -> 更新开关
    set_ml_state(model=model, threshold=threshold, active=bool(res["pass_gate"]))
    state = get_ml_state()
    return jsonify({"metrics": res, "state": state})

@app.get("/ml/state")
def ml_state_route() -> Any:
    return jsonify(get_ml_state())
# debug: run Channel-2 directly
@app.get("/debug/pattern")
def debug_pattern():
    domain = (request.args.get("domain") or "").strip()
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None
    if not domain:
        return jsonify({"error":"domain required"}), 400
    from crawler_core.scraping.patterns import PatternCrawler, get_rule_for
    rule = get_rule_for(domain)
    if not rule:
        return jsonify({"domain": domain, "enabled": False, "items": []})
    pc = PatternCrawler(domain=domain, keywords=[])
    items = pc.crawl(start_date=start_date, end_date=end_date)
    return jsonify({"domain": domain, "enabled": True, "count": len(items), "items": items[:20]})

@app.post("/ml/disable")
def ml_disable_route() -> Any:
    st = get_ml_state()
    set_ml_state(model=st.get("model","nb"), threshold=float(st.get("threshold",0.7)), active=False)

    return jsonify(get_ml_state())
