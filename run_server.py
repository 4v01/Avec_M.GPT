from __future__ import annotations
import os, sys, threading, time, webbrowser
from pathlib import Path

def _fix_sys_path():
    # dev: add ./src; frozen: add runtime dir and MEIPASS
    if getattr(sys, "frozen", False):
        runtime = Path(sys.executable).parent
        meipass = Path(getattr(sys, "_MEIPASS", runtime))
        for p in (runtime, meipass):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
    else:
        here = Path(__file__).resolve().parent
        src = (here / "src").resolve()
        if src.exists() and str(src) not in sys.path:
            sys.path.insert(0, str(src))

_fix_sys_path()

# load .env from runtime dir OR from MEIPASS (onefile add-data)
try:
    from dotenv import load_dotenv
    if getattr(sys, "frozen", False):
        runtime = Path(sys.executable).parent
        meipass = Path(getattr(sys, "_MEIPASS", runtime))
        for cand in (runtime / ".env", meipass / ".env"):
            if cand.exists():
                load_dotenv(str(cand))
                break
    else:
        env_file = Path(__file__).resolve().parent / ".env"
        if env_file.exists():
            load_dotenv(str(env_file))
except Exception:
    pass

from crawler_core.api.app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    # auto-open browser for HR
    def _open():
        time.sleep(0.7)
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False)
