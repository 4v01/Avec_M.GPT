from __future__ import annotations
import logging, os
from logging.handlers import RotatingFileHandler
from crawler_core.config import resolve_paths

def setup_logging(level: int = logging.INFO) -> None:
    paths = resolve_paths()
    log_dir = os.path.join(paths.var_dir or ".", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "app.log")
    root = logging.getLogger()
    if root.handlers: return
    root.setLevel(level)
    ch = logging.StreamHandler(); ch.setLevel(level)
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    root.addHandler(ch)
    fh = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=3, encoding="utf-8")
    fh.setLevel(level); fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(fh)
