"""ASX Neon Terminal: serves the screen and normalised market data on this computer only."""
import json
import os
import re
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()


load_env()
from providers import get_provider  # noqa: E402
from providers.base import DataUnavailable  # noqa: E402

provider = get_provider()
CODE = re.compile(r"^[A-Z0-9]{1,6}$")
TF = {"1D", "5D", "1M", "6M", "1Y"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT / "web"), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, body, status=200):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, OSError):
            pass  # the page was closed or refreshed mid-request

    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return super().do_GET()
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            self.send_json({"ok": True, "data": self.route(url.path[5:], q)})
        except DataUnavailable as e:
            self.send_json({"ok": False, "error": str(e) or "暂不可用"})
        except ValueError as e:
            self.send_json({"ok": False, "error": str(e)}, 400)
        except Exception as e:  # never expose internals or the key to the page
            print(f"[{url.path}] {type(e).__name__}")
            self.send_json({"ok": False, "error": "服务器内部错误"}, 500)

    def route(self, name, q):
        if name == "status":
            return {"provider": provider.name, "latency": provider.latency,
                    "capabilities": provider.capabilities}
        if name == "version":
            return {"web": max(f.stat().st_mtime_ns for f in (ROOT / "web").glob("*") if f.is_file())}
        if name == "update":
            changed = do_update()
            if changed:  # restart a moment after answering, so the new code takes over
                threading.Timer(1.0, restart).start()
            return {"code_changed": changed}
        if name == "study":
            f = ROOT / "data" / "study_results.json"
            if f.exists():
                return json.loads(f.read_text(encoding="utf-8"))
            import urllib.request
            url = "https://raw.githubusercontent.com/pbz13p/asx-terminal/results/study_results.json"
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    return json.load(r)
            except Exception:
                raise DataUnavailable("研究结果还没生成") from None
        if name == "scan":
            import urllib.request
            hit = getattr(self.server, "_scan", None)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            url = "https://raw.githubusercontent.com/pbz13p/asx-terminal/results/study22_results.json"
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    data = json.load(r)
            except Exception:
                raise DataUnavailable("今日扫描还没生成") from None
            self.server._scan = (time.time(), data)
            return data
        if name == "market":
            return provider.market()
        if name == "indices":
            return provider.indices()
        if name == "quotes":
            codes = [c for c in q.get("codes", "").upper().split(",") if c]
            if not codes or len(codes) > 40 or not all(CODE.match(c) for c in codes):
                raise ValueError("股票代码不正确")
            return provider.quotes(codes)
        code = q.get("code", "").upper()
        if not CODE.match(code):
            raise ValueError("股票代码不正确")
        if name == "insight":
            from insight import insight
            return insight(code, provider)
        if name == "vol":
            from research import analyse
            years = max(3, min(30, int(q.get("years", "20"))))
            return analyse(code, provider.history(code, years))
        if name == "candles":
            if q.get("tf") not in TF:
                raise ValueError("周期不正确")
            return provider.candles(code, q["tf"])
        if name == "sales":
            return provider.sales(code)
        if name == "book":
            return provider.book(code)
        raise ValueError("未知接口")


CODE_FILES = ("server.py", "insight.py", "research.py", "update.py", "providers/eodhd.py", "providers/base.py",
              "providers/ibkr.py", "providers/__init__.py")


def _stamp():
    return {f: (ROOT / f).stat().st_mtime_ns for f in CODE_FILES if (ROOT / f).exists()}


_update_lock = threading.Lock()


def do_update():
    """Pull the latest files from GitHub now. Returns True when program code (not just pages) changed."""
    import importlib
    import update
    with _update_lock:
        before = _stamp()
        try:
            importlib.reload(update).main()
        except Exception:
            pass
        return _stamp() != before


def restart():
    import sys
    print("Program updated, restarting...")
    os.environ["ASX_DESK_RESTART"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


def auto_update():
    while True:
        time.sleep(60)
        if do_update():  # program code changed: restart in place so new features work
            restart()


def main():
    threading.Thread(target=auto_update, daemon=True).start()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ASX Neon Terminal 已启动: http://127.0.0.1:{port}  (关闭此窗口即停止)")
    if os.environ.get("NO_BROWSER") != "1":
        if not os.environ.get("ASX_DESK_RESTART"):
            webbrowser.open(f"http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
