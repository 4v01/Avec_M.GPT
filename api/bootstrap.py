# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
VAR_DIR: Path = PROJECT_ROOT / "var"
LOGS_DIR: Path = VAR_DIR / "logs"
API_LOGS_DIR: Path = PROJECT_ROOT / "api" / "var" / "logs"
WEB_DIR: Path = PROJECT_ROOT / "web"

for _p in (VAR_DIR, LOGS_DIR, API_LOGS_DIR):
    _p.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("PRJ_ROOT", str(PROJECT_ROOT))
os.environ.setdefault("PRJ_VAR", str(VAR_DIR))
os.environ.setdefault("PRJ_LOGS", str(LOGS_DIR))
