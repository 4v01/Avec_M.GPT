from __future__ import annotations
import os, sqlite3, csv
from datetime import datetime
from typing import List, Optional, Tuple, Iterable, Dict, Any

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "..", "var", "crawler.db")
_DB_PATH = os.path.abspath(_DB_PATH)

def _conn() -> sqlite3.Connection:
    return sqlite3.connect(_DB_PATH)

def init_db() -> None:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    with _conn() as con:
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS site_mapping (
            name TEXT PRIMARY KEY, domain TEXT, last_updated TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS training_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, label INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS crawl_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, title TEXT, url TEXT, source TEXT, date TEXT,
            excerpt TEXT, predicted_label INTEGER, human_label INTEGER,
            keywords TEXT, media_names TEXT, created_at TIMESTAMP)""")
        # —— ML 评估记录与状态 —— #
        cur.execute("""CREATE TABLE IF NOT EXISTS ml_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, model TEXT, precision REAL, recall REAL, f1 REAL,
            n_train INTEGER, n_test INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS ml_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            model TEXT, threshold REAL, active INTEGER, updated_at TEXT)""")
        # 确保有一行状态
        cur.execute("""INSERT OR IGNORE INTO ml_state(id, model, threshold, active, updated_at)
                       VALUES (1,'nb',0.70,0,?)""", (datetime.utcnow().isoformat(),))
        con.commit()

def add_site_mapping(name: str, domain: str) -> None:
    with _conn() as con:
        con.execute("INSERT OR REPLACE INTO site_mapping(name, domain, last_updated) VALUES (?,?,?)",
                    (name, domain, datetime.utcnow().isoformat()))
        con.commit()

def get_site_domain(name: str) -> Optional[str]:
    with _conn() as con:
        cur = con.execute("SELECT domain FROM site_mapping WHERE name=?", (name,))
        row = cur.fetchone()
        return row[0] if row else None

def add_training_sample(text: str, label: int) -> None:
    with _conn() as con:
        con.execute("INSERT INTO training_data(text, label) VALUES(?,?)", (text, int(label)))
        con.commit()

def get_training_data(limit: Optional[int] = None) -> List[Tuple[str, int]]:
    with _conn() as con:
        q = "SELECT text, label FROM training_data ORDER BY id DESC"
        if limit: q += " LIMIT ?"
        cur = con.execute(q, (() if limit is None else (limit,)))
        return [(r[0], int(r[1])) for r in cur.fetchall()]

# —— 本轮复验入库 & 导出 —— #
def save_review_results(run_id: str, items: Iterable[Dict[str, Any]],
                        keywords: str, media_names: str) -> int:
    cnt = 0
    with _conn() as con:
        cur = con.cursor()
        now = datetime.utcnow().isoformat()
        for a in items:
            title = a.get("title",""); url = a.get("url","")
            source = a.get("source",""); date = a.get("date","")
            excerpt = a.get("excerpt","")
            pred = int(a.get("predicted_label", 0))
            human = int(a.get("human_label", pred))
            cur.execute("""INSERT INTO crawl_results
                (run_id,title,url,source,date,excerpt,predicted_label,human_label,keywords,media_names,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id,title,url,source,date,excerpt,pred,human,keywords,media_names,now))
            text = f"{title}\n{excerpt}".strip()
            if text:
                cur.execute("INSERT INTO training_data(text,label) VALUES(?,?)", (text, human))
            cnt += 1
        con.commit()
    return cnt

def export_run_to_csv(run_id: str, export_dir: str) -> str:
    os.makedirs(export_dir, exist_ok=True)
    with _conn() as con:
        cur = con.execute("""SELECT title,url,source,date,excerpt,predicted_label,human_label,keywords,media_names,created_at
                             FROM crawl_results WHERE run_id=? ORDER BY id ASC""", (run_id,))
        rows = cur.fetchall()
    out_path = os.path.join(export_dir, f"crawl_results_{run_id}.csv")
    import csv
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["标题","URL","来源","时间","内容节选","模型预测","人工标签","关键词","媒体","写入时间"])
        for r in rows: w.writerow(r)
    return os.path.abspath(out_path)

# —— ML 评估记录 & 状态 —— #
def record_ml_run(model: str, precision: float, recall: float, f1: float, n_train: int, n_test: int) -> None:
    with _conn() as con:
        con.execute("""INSERT INTO ml_runs(ts,model,precision,recall,f1,n_train,n_test)
                       VALUES (?,?,?,?,?,?,?)""",
                    (datetime.utcnow().isoformat(), model, precision, recall, f1, n_train, n_test))
        con.commit()

def set_ml_state(model: str, threshold: float, active: bool) -> None:
    with _conn() as con:
        con.execute("""UPDATE ml_state SET model=?, threshold=?, active=?, updated_at=?
                       WHERE id=1""",
                    (model, float(threshold), 1 if active else 0, datetime.utcnow().isoformat()))
        con.commit()

def get_ml_state() -> Dict[str, Any]:
    with _conn() as con:
        cur = con.execute("SELECT model, threshold, active, updated_at FROM ml_state WHERE id=1")
        row = cur.fetchone()
        if not row: return {"model":"nb","threshold":0.7,"active":0,"updated_at":""}
        return {"model":row[0], "threshold":float(row[1]), "active":int(row[2]), "updated_at":row[3]}
