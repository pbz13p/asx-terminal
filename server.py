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
            return {"web": (ROOT / "web" / "index.html").stat().st_mtime_ns}
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
        if name == "candles":
            if q.get("tf") not in TF:
                raise ValueError("周期不正确")
            return provider.candles(code, q["tf"])
        if name == "sales":
            return provider.sales(code)
        if name == "book":
            return provider.book(code)
        raise ValueError("未知接口")


def auto_update():
    import update
    while True:
        time.sleep(300)
        try:
            update.main()
        except Exception:
            pass


def main():
    threading.Thread(target=auto_update, daemon=True).start()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ASX Neon Terminal 已启动: http://127.0.0.1:{port}  (关闭此窗口即停止)")
    if os.environ.get("NO_BROWSER") != "1":
        webbrowser.open(f"http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
