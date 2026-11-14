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


def fetch_result(name, timeout=30, branch=None):
    """A results-branch file: the encrypted .enc copy when ASX_DATA_KEY is set, else the plain one."""
    import urllib.request
    branch = branch or ("cn-results" if name.startswith("cn_") else "results")
    base = f"https://raw.githubusercontent.com/pbz13p/asx-terminal/{branch}/"
    if os.environ.get("ASX_DATA_KEY"):
        try:
            import securedata
            with urllib.request.urlopen(base + name + ".enc", timeout=timeout) as r:
                return json.loads(securedata.decrypt(r.read()))
        except Exception:
            pass
    with urllib.request.urlopen(base + name, timeout=timeout) as r:
        return json.load(r)
from providers import get_provider  # noqa: E402
from providers.base import DataUnavailable  # noqa: E402

provider = get_provider()
CODE = re.compile(r"^[A-Z0-9]{1,6}$")
CN_CODE = re.compile(r"^\d{6}$")
_cnp = None
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

    # ---- private login when hosted (SITE_USERS="frank:password1,baba:password2" in .env) ----
    def _users(self):
        out = {}
        for part in os.environ.get("SITE_USERS", "").split(","):
            if ":" in part:
                u, pw = part.split(":", 1)
                out[u.strip()] = pw.strip()
        return out

    def _token(self, user):
        import hashlib
        import hmac
        secret = (os.environ.get("SITE_SECRET") or os.environ.get("ASX_DATA_KEY") or "local").encode()
        return user + "." + hmac.new(secret, user.encode(), hashlib.sha256).hexdigest()[:40]

    def _logged_in(self):
        users = self._users()
        if not users:
            return True  # local use on your own PC: no login
        from http.cookies import SimpleCookie
        c = SimpleCookie(self.headers.get("Cookie") or "")
        tok = c["ntsess"].value if "ntsess" in c else ""
        user = tok.split(".", 1)[0]
        import hmac
        return bool(user in users and hmac.compare_digest(tok, self._token(user)))

    def _login_page(self, msg=""):
        body = f"""<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Neon Terminal</title><link href="/fonts/fonts.css" rel=stylesheet>
<body style="margin:0;height:100vh;display:grid;place-items:center;background:#05060C;color:#E6ECFF;font:15px 'JetBrains Mono','Microsoft YaHei',monospace">
<form method=post action=/login style="width:300px;padding:28px;border:1px solid #22E4F5;border-radius:12px;background:#0A0D18;box-shadow:0 0 30px rgba(34,228,245,.2)">
<div style="font:700 18px 'Space Grotesk';margin-bottom:18px"><span style="color:#22E4F5">●</span> NEON <b style="color:#22E4F5">TERMINAL</b></div>
<input name=u placeholder="用户名" autocomplete=username style="width:100%;height:38px;margin-bottom:10px;background:#0E1222;border:1px solid #222B4D;border-radius:6px;color:#fff;padding:0 10px">
<input name=p type=password placeholder="密码" autocomplete=current-password style="width:100%;height:38px;margin-bottom:14px;background:#0E1222;border:1px solid #222B4D;border-radius:6px;color:#fff;padding:0 10px">
<button style="width:100%;height:40px;border:0;border-radius:6px;background:#22E4F5;color:#03050A;font-weight:700;cursor:pointer">登录</button>
<div style="color:#FF2E74;margin-top:10px;font-size:13px">{msg}</div></form></body></html>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/login":
            self.send_error(404)
            return
        from urllib.parse import parse_qs as _pq
        n = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in _pq(self.rfile.read(min(n, 4096)).decode("utf-8", "replace")).items()}
        u, pw = form.get("u", "").strip(), form.get("p", "")
        users = self._users()
        import hmac
        if u in users and hmac.compare_digest(pw, users[u]):
            self.send_response(303)
            self.send_header("Set-Cookie", f"ntsess={self._token(u)}; Path=/; Max-Age=7776000; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/")
            self.end_headers()
            return
        time.sleep(1.5)  # slow down password guessing
        self._login_page("用户名或密码不对")

    def do_GET(self):
        url = urlparse(self.path)
        if not self._logged_in() and not url.path.startswith("/fonts/"):
            if url.path.startswith("/api/"):
                self.send_json({"ok": False, "error": "请先登录"}, 401)
                return
            return self._login_page()
        if self._users() and url.path == "/api/update":
            self.send_json({"ok": False, "error": "网上版本会自动更新"}, 403)
            return
        if url.path == "/report.html" and (ROOT / "web" / "report.html.enc").exists():
            return self.send_report()
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

    def do_HEAD(self):
        return self.do_GET()

    def send_report(self):
        enc = ROOT / "web" / "report.html.enc"
        try:
            import securedata
            body = securedata.decrypt(enc.read_bytes())
        except Exception as e:
            plain = ROOT / "web" / "report.html"
            if plain.exists():
                return super().do_GET()
            msg = "研究报告是加密的：请在 App 文件夹的 .env 里加上 ASX_DATA_KEY=（密钥），再重启 App。"
            body = f"<!doctype html><meta charset=utf-8><body style='background:#05060F;color:#EEF2FF;font:20px sans-serif;padding:40px'>{msg}</body>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def research(self):
        """Research centre: the findings registry (research/findings.json.enc, updated after every round) plus today's
        stocks that match each finding, taken from the daily scan and the volatility model."""
        import securedata
        f = ROOT / "research" / "findings.json.enc"
        plain = ROOT / "research" / "findings.json"
        try:
            reg = json.loads(securedata.decrypt(f.read_bytes()))
        except Exception:
            if plain.exists():
                reg = json.loads(plain.read_text(encoding="utf-8"))
            else:
                raise DataUnavailable("研究结论是加密的：请在 .env 里加上 ASX_DATA_KEY") from None
        today, scan_day = {}, None
        try:
            scan = self.route("scan", {})
            scan_day = scan.get("last_day")
            today["rockets"] = [{"code": r["code"], "note": ("今天" if r.get("days") == 0 else f"{r.get('days')} 天前") + (" · 可交易" if r.get("tradable") else "")}
                                for r in scan.get("rockets") or []]
            today["watch_alerts"] = [{"code": r["code"], "note": "；".join(r.get("alerts") or [])} for r in scan.get("watch") or [] if r.get("alerts")]
            today["breakouts"] = [{"code": r["code"], "note": f"放量 {r.get('vol_x')}×"} for r in scan.get("breakouts") or []]
        except Exception:
            pass
        try:
            v = self.route("volml", {})
            today["vol_alerts"] = [{"code": r["code"], "note": f"20 天预测波动 {r.get('vol20')}%（历史 {r.get('pct20')} 分位）"} for r in v.get("alerts") or []][:30]
        except Exception:
            pass
        try:
            picks = fetch_result("cn_picks.json")
            today["cn_top"] = [{"code": r["code"], "note": (r.get("name") or "") + f" · 模型分 {r.get('score')}"} for r in picks.get("top") or []]
            today["cn_avoid"] = [{"code": r["code"], "note": r.get("name") or ""} for r in picks.get("avoid") or []][:40]
            reg["cn_day"] = picks.get("date")
        except Exception:
            pass
        for c in reg.get("cards", []):
            c["hits"] = today.get(c.get("today") or "", []) if c.get("today") else None
        reg["scan_day"] = scan_day
        return reg

    def profile(self, code):
        """What our research says about one stock: today's scan lists it appears in, volatility model, watch alerts."""
        out = {"code": code, "lists": [], "watch": None, "vol": None}
        try:
            scan = self.route("scan", {})
            for key, label in (("rockets", "爆拉候选"), ("breakouts", "放量突破"), ("sleeve_candidates", "小仓位候选"),
                               ("season_gap_up", "业绩季跳空高开"), ("season_gap_down", "业绩季跳空低开"), ("halt_reopen_down", "复牌下跌")):
                for r in scan.get(key) or []:
                    if r.get("code") == code:
                        out["lists"].append({"list": label, **r})
            for r in scan.get("watch") or []:
                if r.get("code") == code:
                    out["watch"] = r
            out["scan_day"] = scan.get("last_day")
        except Exception:
            pass
        try:
            v = self.route("volml", {})
            out["vol"] = next((r for r in v.get("latest") or [] if r.get("code") == code), None)
        except Exception:
            pass
        return out

    def route_cn(self, name, q):
        """A-share mode (?m=cn): same endpoints, China data and rules."""
        from providers.eodhd_cn import CnProvider
        global _cnp
        if _cnp is None:
            _cnp = CnProvider()
        if name == "market":
            return _cnp.market()
        if name == "indices":
            return _cnp.indices()
        if name == "quotes":
            codes = [c for c in q.get("codes", "").split(",") if c]
            if not codes or len(codes) > 40 or not all(CN_CODE.match(c) for c in codes):
                raise ValueError("A 股代码是 6 位数字")
            return _cnp.quotes(codes)
        code = q.get("code", "")
        if not CN_CODE.match(code):
            raise ValueError("A 股代码是 6 位数字")
        if name == "candles":
            if q.get("tf") not in TF:
                raise ValueError("周期不正确")
            return _cnp.candles(code, q["tf"])
        if name == "insight":
            return _cnp.insight(code)
        if name == "ann":
            return _cnp.announcements(code)
        if name == "profile":
            out = {"code": code, "lists": [], "watch": None, "vol": None}
            try:
                picks = fetch_result("cn_picks.json")
                for key, label in (("top", "A 股模型选股（前 10%）"), ("avoid", "A 股模型避开（后 10%）")):
                    for r in picks.get(key) or []:
                        if r.get("code") == code:
                            out["lists"].append({"list": label, **r})
            except Exception:
                pass
            return out
        raise ValueError("A 股模式暂不支持这个功能")

    def route(self, name, q):
        if q.get("m") == "cn" and name in ("market", "indices", "quotes", "candles", "insight", "ann", "profile"):
            return self.route_cn(name, q)
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
            try:
                return fetch_result("study_results.json")
            except Exception:
                raise DataUnavailable("研究结果还没生成") from None
        if name == "scan":
            import urllib.request
            hit = getattr(self.server, "_scan", None)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            try:
                data = fetch_result("study22_results.json")
            except Exception:
                raise DataUnavailable("今日扫描还没生成") from None
            self.server._scan = (time.time(), data)
            return data
        if name == "volml":
            import urllib.request
            hit = getattr(self.server, "_volml", None)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            try:
                data = fetch_result("study61_results.json")
            except Exception:
                raise DataUnavailable("波动率模型结果还没生成") from None
            data.pop("results_per_stock", None)
            self.server._volml = (time.time(), data)
            return data
        if name == "research":
            return self.research()
        if name == "cnpicks":
            try:
                return fetch_result("cn_picks.json")
            except Exception:
                raise DataUnavailable("A 股每日选股还没生成") from None
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
        if name in ("ann", "anntext", "anntr"):
            import annsvc
            if name == "ann":
                return annsvc.listing(code)
            ids = q.get("ids", "")
            return annsvc.text(code, ids) if name == "anntext" else annsvc.translate(code, ids)
        if name == "profile":
            return self.profile(code)
        if name == "book":
            return provider.book(code)
        raise ValueError("未知接口")


CODE_FILES = ("server.py", "providers/eodhd_cn.py", "securedata.py", "annsvc.py", "study67.py", "insight.py", "research.py", "update.py", "providers/eodhd.py", "providers/base.py",
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
    if os.environ.get("ASX_DESK_NO_UPDATE"):  # developer test runs: never overwrite local edits
        return
    while True:
        time.sleep(60)
        if do_update():  # program code changed: restart in place so new features work
            restart()


def main():
    threading.Thread(target=auto_update, daemon=True).start()
    port = int(os.environ.get("PORT", "8765"))
    host = os.environ.get("HOST", "127.0.0.1")  # the hosted copy listens behind Caddy on 127.0.0.1 too
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"ASX Neon Terminal 已启动: http://127.0.0.1:{port}  (关闭此窗口即停止)")
    if os.environ.get("NO_BROWSER") != "1":
        if not os.environ.get("ASX_DESK_RESTART"):
            webbrowser.open(f"http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
