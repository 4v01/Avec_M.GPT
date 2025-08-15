from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os, sys

@dataclass
class Paths:
    base_dir: str
    var_dir: str
    frontend_dir: str

def resolve_paths() -> Paths:
    if getattr(sys, "frozen", False):
        runtime = Path(sys.executable).parent
        meipass = Path(getattr(sys, "_MEIPASS", runtime))
        fe_env = os.getenv("CRAWLER_FE_DIR")
        var_env = os.getenv("CRAWLER_VAR_DIR")

        fe = Path(fe_env) if fe_env else (
            (runtime / "frontend") if (runtime / "frontend").exists() else (meipass / "frontend")
        )
        var = Path(var_env) if var_env else (runtime / "var")
        var.mkdir(parents=True, exist_ok=True)
        return Paths(str(runtime), str(var), str(fe))

    root = Path(__file__).resolve().parents[3]
    fe = Path(os.getenv("CRAWLER_FE_DIR") or (root / "frontend"))
    var = Path(os.getenv("CRAWLER_VAR_DIR") or (root / "var"))
    var.mkdir(parents=True, exist_ok=True)
    return Paths(str(root), str(var), str(fe))
