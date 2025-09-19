from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

try:
    import appdirs  # type: ignore
except Exception:
    appdirs = None

__all__ = [
    "paths",
    "read_json",
    "write_json",
    "ensure_parent",
    "get_env_bool",
    "get_env_int",
]


def _user_data_root() -> Path:
    env_root = os.getenv("APP_DATA_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    if appdirs:
        return Path(appdirs.user_data_dir("pr_platform", "mespion")).resolve()
    return Path.cwd().joinpath("data").resolve()


DATA_ROOT = _user_data_root()
LOGS_ROOT = Path(os.getenv("APP_LOGS_ROOT", str(DATA_ROOT / "logs"))).resolve()

paths: Dict[str, Path] = {
    "data_root": DATA_ROOT,
    "logs_root": LOGS_ROOT,
    "scrapyd_jobs": DATA_ROOT / "scrapyd_jobs.json",
    "jobs_cache": DATA_ROOT / "jobs_cache.json",
    "last_payload": DATA_ROOT / "last_payload.json",
}


def ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path: Path | str, data: Any) -> None:
    p = Path(path)
    ensure_parent(p)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def get_env_int(name: str, default: int = 0) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    try:
        return int(v.strip())
    except Exception:
        return default
