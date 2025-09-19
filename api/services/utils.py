# -*- coding: utf-8 -*-
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
STATE = VAR / "state"
OUT = VAR / "out"
STATE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

PROGRESS_JSON = STATE / "progress.json"

def write_progress(payload: dict):
    PROGRESS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def read_progress() -> dict:
    try:
        return json.loads(PROGRESS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {"phase":"idle","percent":0,"message":"ready","items":[]}
