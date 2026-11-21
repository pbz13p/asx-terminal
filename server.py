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
_cxp = None
_cmp = None
CM_CODE = re.compile(r"^[A-Z0-9]{2,12}$")
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
CX_CODE = re.compile(r"^[A-Z0-9]{1,15}$")
TF = {"1D", "5D", "1M", "6M", "1Y", "5Y", "ALL"}


# Evidence tiers for research cards (Frank 2026-11-15: "逻辑通不等于能挣钱" -- show how far each finding has been proven).
# 只是讲得通 -> 回测过 -> 实盘撑住. Only the live track record (out/track_*.json, started 2026-11-15) can lift a card to the top.
TRACK_OF = {"rockets": ("au", "rockets_tradable", "rockets"), "cn_top": ("cn", "cn_top"), "cn_avoid": ("cn", "cn_avoid")}
LIVE_MIN_SIGNALS, LIVE_MIN_DAYS = 30, 60          # at least 30 priced signals and 60 calendar days (~40 trading days)
LIVE_COST = {"au": 3.0, "cn": 1.0}                # % round trip: ASX 3% control figure (CLAUDE.md); A-share 1% conservative


def card_tier(card, track, today=None):
    """-> (key, label, note). key: bad / tool / weak / backtest / live_ok / live_fail."""
    from datetime import date
    v = card.get("verdict")
    if v == "无效":
        return "bad", "测过无效", "回测证伪，留着防止重复测"
    if v == "工具":
        return "tool", "工具", "辅助判断用，不是赚钱规则"
    if v != "有效":
        return "weak", "只是讲得通", "回测证据不够（效果弱、不稳定或没扣完成本）"
    spec = TRACK_OF.get(card.get("today") or "")
    if not spec:
        return "backtest", "回测过", "还没接实盘记录，不能确认现在还有效"
    mk, ids = spec[0], spec[1:]
    lists = ((track or {}).get(mk) or {}).get("lists") or {}
    t = next((lists[i] for i in ids if i in lists), None)
    if not t or not t.get("first") or t.get("avg_vs_index") is None:
        return "backtest", "回测过", "实盘记录刚开始，还没有结果"
    days = ((today or date.today()) - date.fromisoformat(t["first"])).days
    n = t.get("priced") or 0
    if n < LIVE_MIN_SIGNALS or days < LIVE_MIN_DAYS:
        return "backtest", "回测过", f"实盘已记 {n} 个信号、{days} 天，要满 {LIVE_MIN_SIGNALS} 个、{LIVE_MIN_DAYS} 天才判断"
    ex = t["avg_vs_index"]
    if card.get("today") == "cn_avoid":          # an avoid list works when those stocks lag the index
        ok, msg = ex < 0, f"实盘 {n} 个信号平均比指数 {ex:+.2f}%（避开名单，跑输才算对）"
    else:
        cost = LIVE_COST[mk]
        ok, msg = ex - cost > 0, f"实盘 {n} 个信号平均比指数 {ex:+.2f}%，扣 {cost:g}% 来回成本后 {ex - cost:+.2f}%"
    return ("live_ok", "实盘撑住", msg) if ok else ("live_fail", "实盘没撑住", msg)


NEWS = ROOT / "data" / "news.json"   # data/ is gitignored: news lives only on this PC
_news_lock = threading.Lock()


def news_load():
    try:
        return json.loads(NEWS.read_text(encoding="utf-8"))
    except Exception:
        return []


_news_synced = [0.0]


def news_sync():
    """2026-11-21: the news task often cannot reach this PC's Chrome, so it also commits its items to the `news` branch
    (news.json, public market-news summaries only). Pull that file at most every 10 minutes and merge (de-duplicated)."""
    if time.time() - _news_synced[0] < 600:
        return
    _news_synced[0] = time.time()
    try:
        import urllib.request
        url = "https://raw.githubusercontent.com/pbz13p/asx-terminal/news/news.json"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}), timeout=15) as r:
            items = json.load(r)
        if isinstance(items, list):
            news_add(items)
    except Exception as e:
        print("news sync:", type(e).__name__, flush=True)


def news_add(items):
    """Add news items (only-add, de-duplicated by id or title). Item: {time, title, what, why, impact, tags[], sources[{name,url}]}."""
    keep = ("id", "time", "title", "what", "why", "impact", "tags", "sources", "tickers")
    with _news_lock:
        cur = news_load()
        seen = {x.get("id") or x.get("title") for x in cur}
        added = 0
        for it in items:
            if not isinstance(it, dict) or not str(it.get("title") or "").strip():
                continue
            it = {k: it[k] for k in keep if k in it}
            it["id"] = str(it.get("id") or it["title"])[:200]
            it.setdefault("time", time.strftime("%Y-%m-%d %H:%M"))
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            cur.append(it)
            added += 1
        cur.sort(key=lambda x: str(x.get("time", "")), reverse=True)
        NEWS.parent.mkdir(exist_ok=True)
        tmp = NEWS.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(NEWS)
        return {"added": added, "total": len(cur)}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT / "web"), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")  # private site: never in Google
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

    def _user(self):
        """Logged-in user name; 'local' on your own PC (no login)."""
        users = self._users()
        if not users:
            return "local"
        from http.cookies import SimpleCookie
        c = SimpleCookie(self.headers.get("Cookie") or "")
        tok = c["ntsess"].value if "ntsess" in c else ""
        return tok.split(".", 1)[0] if self._logged_in() else None

    def _is_owner(self):
        u = self._user()
        return u == "local" or (u is not None and u == next(iter(self._users()), None))  # first account in SITE_USERS = Frank

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
        path = urlparse(self.path).path
        if path in ("/api/chat", "/api/idea"):
            if not self._logged_in():
                self.send_json({"ok": False, "error": "请先登录"}, 401)
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 20000)).decode("utf-8") or "{}")
                import aichat
                if path == "/api/chat":
                    data = aichat.chat(self, self._user(), body.get("text"), "cn" if body.get("m") == "cn" else "au", (body.get("code") or "")[:6])
                else:
                    data = aichat.send_idea(self._user(), str(body.get("id", "")), body.get("note", ""))
                self.send_json({"ok": True, "data": data})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)[:300]}, 200)
            return
        if path == "/api/seal":   # own-PC only: encrypt a findings file with the local key (the key never leaves this PC)
            if self._users() or self.client_address[0] != "127.0.0.1":
                self.send_error(404)
                return
            import base64
            import securedata
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(n, 5_000_000))
            sealed = securedata.encrypt(raw)
            out = ROOT / "sealed"   # Claude picks the sealed file up from this folder (the key never leaves this PC)
            out.mkdir(exist_ok=True)
            name = re.sub(r"[^\w.-]", "", self.headers.get("X-Name") or "findings.json.enc")
            (out / name).write_bytes(sealed)
            self.send_json({"ok": True, "data": {"saved": str(out / name), "bytes": len(sealed)}})
            return
        if path == "/api/seal_dir":   # own-PC only: bundle sealed/<folder>/*.json (transcripts) into one encrypted file
            if self._users() or self.client_address[0] != "127.0.0.1":
                self.send_error(404)
                return
            import gzip
            import securedata
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 10_000)) or b"{}")
            folder = re.sub(r"[^\w-]", "", body.get("folder", ""))[:40]
            src = ROOT / "sealed" / folder
            bundle = {}
            for f in sorted(src.glob("*.json")):
                if f.name.startswith("_"):
                    continue
                d = json.loads(f.read_text(encoding="utf-8"))
                bundle[f.stem] = {"title": d.get("original_title"), "duration": d.get("duration"),
                                  "lines": [[round(x.get("t", 0)), x.get("text", "")] for x in d.get("lines", [])]}
            name = folder + "_bundle.json.gz.enc"
            (ROOT / "sealed" / name).write_bytes(securedata.encrypt(gzip.compress(json.dumps(bundle, ensure_ascii=False).encode())))
            self.send_json({"ok": True, "data": {"saved": str(ROOT / "sealed" / name), "videos": len(bundle)}})
            return
        if path == "/api/earwise_batch":   # own-PC only: same as /api/earwise for a list of videos, in a background thread
            if self._users() or self.client_address[0] != "127.0.0.1":
                self.send_error(404)
                return
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 200000)) or b"{}")
            folder = re.sub(r"[^\w-]", "", body.get("folder", "earwise"))[:40]
            vids = [re.sub(r"[^\w-]", "", v)[:20] for v in body.get("vids", [])][:1000]
            threading.Thread(target=_earwise_batch, args=(vids, str(body.get("token", "")), folder), daemon=True).start()
            self.send_json({"ok": True, "data": {"queued": len(vids)}})
            return
        if path == "/api/earwise":   # own-PC only: fetch one video's full transcript from Frank's 慧听 and keep it in sealed/<folder>
            if self._users() or self.client_address[0] != "127.0.0.1":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
                import urllib.request
                vid = re.sub(r"[^\w-]", "", body.get("vid", ""))[:20]
                out = ROOT / "sealed" / re.sub(r"[^\w-]", "", body.get("folder", "earwise"))[:40]   # the folder Claude can pick files up from
                out.mkdir(parents=True, exist_ok=True)
                f = out / f"{vid}.json"
                if not f.exists():
                    req = urllib.request.Request("https://tingowl.com/captions", method="POST",
                                                 data=json.dumps({"url": f"https://www.youtube.com/watch?v={vid}", "lang": "zh", "translate": False}).encode(),
                                                 headers={"Content-Type": "application/json", "X-App-Token": "kandong2026", "Origin": "https://tingowl.com",
                                                          "User-Agent": BROWSER_UA,
                                                          "Authorization": "Bearer " + str(body.get("token", ""))})
                    with urllib.request.urlopen(req, timeout=1800) as r:
                        f.write_bytes(r.read())
                j = json.loads(f.read_bytes())
                self.send_json({"ok": True, "data": {"saved": str(f), "lines": len(j.get("lines") or []), "source": j.get("source"), "duration": j.get("duration")}})
            except Exception as e:
                self.send_json({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            return
        if path == "/api/news":   # own-PC only: the 经济热点快讯 task saves each pushed news item here (kept on this PC, never in the public repo)
            if self._users() or self.client_address[0] != "127.0.0.1":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 2_000_000)).decode("utf-8") or "{}")
                self.send_json({"ok": True, "data": news_add(body.get("items") or [])})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)[:300]}, 400)
            return
        if path != "/login":
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

    def weekly(self):
        """周报: this week's index/sector moves and leaders (live from EODHD on this PC), Australia/US macro events
        (research/weekly/<friday>.json, written by the Saturday task from public calendars), next week's volatility
        forecast (round 61) and how last week's forecast did (voltrack)."""
        out = {"market": None, "macro": None, "vol": None, "errors": []}
        try:
            out["market"] = provider.weekly()
        except Exception as e:
            out["errors"].append(f"行情：{e}")
        try:
            files = sorted((ROOT / "research" / "weekly").glob("*.json"))
            out["macro"] = json.loads(files[-1].read_text(encoding="utf-8")) if files else None
            out["weeks"] = [f.stem for f in files]
        except Exception as e:
            out["errors"].append(f"宏观：{e}")
        try:
            hit = getattr(self.server, "_volml", None)
            V = hit[1] if hit and time.time() - hit[0] < 600 else fetch_result("study61_results.json")
            L = [x for x in V.get("latest") or [] if x.get("vol5")]
            v5 = sorted(x["vol5"] for x in L)
            up = [x for x in L if x.get("naive_vol5") and x["vol5"] > x["naive_vol5"]]
            out["vol"] = {"generated": V.get("generated"), "asof": max((x.get("date") or "" for x in L), default=None),
                          "n": len(L), "median_vol5": v5[len(v5) // 2] if v5 else None,
                          "up_pct": round(100 * len(up) / len(L)) if L else None,
                          "top": [{k: x.get(k) for k in ("code", "name", "sector", "vol5", "naive_vol5", "pct20", "conf5")}
                                  for x in sorted(L, key=lambda x: -x["vol5"])[:10]],
                          "track": V.get("track")}
        except Exception:
            out["errors"].append("波动率：模型结果还没生成")
        return out

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
            reg.setdefault("track", {})["au"] = scan.get("track")
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
            reg.setdefault("track", {})["cn"] = picks.get("track")
        except Exception:
            pass
        for c in reg.get("cards", []):
            c["hits"] = today.get(c.get("today") or "", []) if c.get("today") else None
            c["tier"], c["tier_label"], c["tier_note"] = card_tier(c, reg.get("track"))
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

    def route_cx(self, name, q):
        """Crypto mode (?m=cx): Binance USDT perpetuals, real time from this PC."""
        from providers.binance import BinanceProvider
        global _cxp
        if _cxp is None:
            _cxp = BinanceProvider()
        if name == "market":
            return _cxp.market()
        if name == "indices":
            return _cxp.indices()
        if name == "quotes":
            codes = [c for c in q.get("codes", "").split(",") if c]
            if not codes or len(codes) > 60 or not all(CX_CODE.match(c) for c in codes):
                raise ValueError("合约代码不正确，例如 BTC")
            return _cxp.quotes(codes)
        code = q.get("code", "")
        if not CX_CODE.match(code):
            raise ValueError("合约代码不正确，例如 BTC")
        if name == "candles":
            if q.get("tf") not in TF:
                raise ValueError("周期不正确")
            return _cxp.candles(code, q["tf"])
        if name == "ann":
            return []
        if name == "profile":
            return {"code": code, "lists": [], "watch": None, "vol": None}
        raise ValueError("加密货币模式暂不支持这个功能")

    def route_cm(self, name, q):
        """Commodity mode (?m=cm): metals and commodity funds (EODHD, delayed) + World Bank monthly prices."""
        from providers.commodities import CommodityProvider
        global _cmp
        if _cmp is None:
            _cmp = CommodityProvider()
        if name == "market":
            return _cmp.market()
        if name == "indices":
            return _cmp.indices()
        if name == "quotes":
            codes = [c for c in q.get("codes", "").split(",") if c]
            if not codes or len(codes) > 60 or not all(CM_CODE.match(c) for c in codes):
                raise ValueError("品种代码不正确，例如 GOLD")
            return _cmp.quotes(codes)
        code = q.get("code", "")
        if not CM_CODE.match(code):
            raise ValueError("品种代码不正确，例如 GOLD")
        if name == "candles":
            if q.get("tf") not in TF:
                raise ValueError("周期不正确")
            return _cmp.candles(code, q["tf"])
        if name == "ann":
            return []
        if name == "profile":
            return {"code": code, "lists": [], "watch": None, "vol": None}
        raise ValueError("商品模式暂不支持这个功能")

    def route(self, name, q):
        if name == "news":
            news_sync()
            return news_load()[:int(q.get("n") or 300)]
        if q.get("m") == "cx" and name in ("market", "indices", "quotes", "candles", "insight", "ann", "profile"):
            return self.route_cx(name, q)
        if q.get("m") == "cm" and name in ("market", "indices", "quotes", "candles", "insight", "ann", "profile"):
            return self.route_cm(name, q)
        if q.get("m") == "cn" and name in ("market", "indices", "quotes", "candles", "insight", "ann", "profile"):
            return self.route_cn(name, q)
        if name == "eodstatus" and not self._users() and self.client_address[0] == "127.0.0.1":   # own PC: why EODHD refuses
            key = getattr(provider, "key", "") or ""
            out = {"key_len": len(key), "key_tail": key[-4:]}
            try:
                u = provider._get("user")
                out.update({k: u.get(k) for k in ("subscriptionType", "paymentMethod", "apiRequests", "dailyRateLimit", "apiRequestsDate", "inviteToken") if k != "inviteToken"})
            except Exception as e:
                out["user_error"] = str(e)
            import providers.eodhd as pe
            out["no_trade_bars"] = hasattr(pe, "_flat")
            for path, kw in (("eod/AXJO.INDX", {"from": "2026-11-10"}), ("real-time/AXJO.INDX", {}), ("real-time/CND.AU", {})):
                try:
                    r = provider._get(path, **kw)
                    out[path + str(kw)] = (f"ok {len(r)} last {r[-1].get('date')}" if isinstance(r, list) and r else str(r)[:160])
                except Exception as e:
                    out[path + str(kw)] = str(e)
            return out
        if name == "where" and not self._users() and self.client_address[0] == "127.0.0.1":   # own PC: app folder path
            return str(ROOT)
        if name == "quick":   # own-PC only: decrypted quick-study results, so Claude can read them without holding the key
            f = q.get("f", "")
            br = q.get("branch", "quick-results")
            if self._users() or self.client_address[0] != "127.0.0.1" or not re.fullmatch(r"(study\d*_results|yc_notes|ann_inventory)\.json", f) \
                    or br not in ("quick-results", "results", "cn-results"):
                raise ValueError("not available")
            data = fetch_result(f, branch=br)
            if q.get("save") == "1" and f in ("yc_notes.json", "study87_results.json", "ann_inventory.json", "study92_results.json", "study94_results.json", "study97_results.json"):   # keep a plain copy on this PC for Claude to pick up
                (ROOT / "sealed").mkdir(exist_ok=True)
                (ROOT / "sealed" / f).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return {"saved": str(ROOT / "sealed" / f), "read": data.get("read")}
            return data
        if name == "annlists":   # round 96 lists (encrypted on the quick-results branch, refreshed after the daily AI read)
            hit = getattr(self.server, "_annlists", None)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            try:
                data = fetch_result("ann_lists.json", branch="quick-results")
            except Exception:
                raise DataUnavailable("公告信号还没生成（每天晚上 AI 读完新公告后更新）") from None
            self.server._annlists = (time.time(), data)
            return data
        if name == "cnbrief":   # 中文公告速读 (cn_brief.py, encrypted on the quick-results branch; private page /cnbrief.html)
            hit = getattr(self.server, "_cnbrief", None)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            try:
                data = fetch_result("cn_brief.json", branch="quick-results")
            except Exception:
                raise DataUnavailable("中文公告速读还没生成（每天晚上读完新公告后更新）") from None
            self.server._cnbrief = (time.time(), data)
            return data
        if name == "ladder":   # round 98 lists (encrypted on the results branch, refreshed after the daily price run)
            hit = getattr(self.server, "_ladder", None)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            try:
                data = fetch_result("ladder_lists.json")
            except Exception:
                raise DataUnavailable("底部反转名单还没生成（每天收盘后更新）") from None
            self.server._ladder = (time.time(), data)
            return data
        if name == "ledger":   # round 94 portfolio ledger (encrypted on the quick-results branch)
            hit = getattr(self.server, "_ledger", None)
            if hit and time.time() - hit[0] < 600:
                return hit[1]
            try:
                data = fetch_result("study94_results.json", branch="quick-results")
            except Exception:
                raise DataUnavailable("组合总账还在计算，稍后再看") from None
            data = {k: v for k, v in data.items() if k != "trades"}
            self.server._ledger = (time.time(), data)
            return data
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
        if name in ("chathist", "ideas", "aiwho"):
            import aichat
            if name == "aiwho":
                return {"user": self._user(), "ai": aichat.provider_for(self._user()), "owner": self._is_owner()}
            if name == "chathist":
                return [m for m in aichat.history(self._user(), 60) if m.get("market") == ("cn" if q.get("m") == "cn" else "au")]
            if not self._is_owner():
                raise ValueError("只有 Frank 能看待审核的想法")
            return aichat.ideas()
        if name == "cnpicks":
            try:
                return fetch_result("cn_picks.json")
            except Exception:
                raise DataUnavailable("A 股每日选股还没生成") from None
        if name == "market":
            return provider.market()
        if name == "indices":
            return provider.indices()
        if name == "board":   # 行业页 (Frank 2026-11-21)
            return provider.board()
        if name == "weekly":   # 周报 (Saturday weekly report, 2026-11-21)
            return self.weekly()
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


CODE_FILES = ("server.py", "providers/binance.py", "providers/commodities.py", "aichat.py", "providers/eodhd_cn.py", "securedata.py", "annsvc.py", "study67.py", "insight.py", "research.py", "update.py", "providers/eodhd.py", "providers/base.py",
              "providers/ibkr.py", "providers/__init__.py")


def _stamp():
    return {f: (ROOT / f).stat().st_mtime_ns for f in CODE_FILES if (ROOT / f).exists()}


_update_lock = threading.Lock()


def _earwise_fetch(vid, token, folder):
    """One video's full transcript from Frank's 慧听 into sealed/<folder>/<vid>.json (kept; never fetched twice)."""
    import urllib.request
    out = ROOT / "sealed" / folder
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{vid}.json"
    if f.exists():
        return "have"
    req = urllib.request.Request("https://tingowl.com/captions", method="POST",
                                 data=json.dumps({"url": f"https://www.youtube.com/watch?v={vid}", "lang": "zh", "translate": False}).encode(),
                                 headers={"Content-Type": "application/json", "X-App-Token": "kandong2026", "Origin": "https://tingowl.com",
                                          "User-Agent": BROWSER_UA, "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=1800) as r:
        f.write_bytes(r.read())
    return "new"


def _earwise_batch(vids, token, folder):
    prog = {"total": len(vids), "done": 0, "new": 0, "failed": {}, "started": time.time()}
    pf = ROOT / "sealed" / folder / "_progress.json"
    pf.parent.mkdir(parents=True, exist_ok=True)
    for v in vids:
        for attempt in range(30):
            try:
                if _earwise_fetch(v, token, folder) == "new":
                    prog["new"] += 1
                prog["failed"].pop(v, None)
                break
            except Exception as e:
                prog["failed"][v] = f"{type(e).__name__}: {str(e)[:120]}"
                time.sleep(30 if "429" in str(e) else 15)
                if attempt >= 3 and "429" not in str(e):
                    break
        prog["done"] += 1
        prog["minutes"] = round((time.time() - prog["started"]) / 60, 1)
        pf.write_text(json.dumps(prog, ensure_ascii=False, indent=1))


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


SERVER = None


def restart():
    """Start a fresh copy and quit this one. 2026-11-17: os.execv on Windows left the PC copy stuck on old code for hours,
    so free the port, launch a new process (it retries the port for a minute) and exit."""
    import subprocess
    import sys
    print("Program updated, restarting...")
    env = {**os.environ, "ASX_DESK_RESTART": "1"}
    try:
        if SERVER is not None:
            SERVER.server_close()
    except Exception:
        pass
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    try:
        subprocess.Popen([sys.executable] + sys.argv, cwd=str(ROOT), env=env, creationflags=flags)
    except Exception:
        os.execve(sys.executable, [sys.executable] + sys.argv, env)
    os._exit(0)


def auto_update():
    if os.environ.get("ASX_DESK_NO_UPDATE"):  # developer test runs: never overwrite local edits
        return
    while True:
        time.sleep(60)
        try:   # never let one bad update kill the updater (it did on 2026-11-16, freezing the PC copy)
            if do_update():  # program code changed: restart so new features work
                restart()
        except Exception as e:
            print("update check failed:", type(e).__name__)


def autostart():
    """Windows: start the terminal by itself after every reboot (minimised window), so Frank never has to click start.bat."""
    if os.name != "nt" or os.environ.get("ASX_DESK_NO_UPDATE"):
        return
    try:
        folder = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
        bat = ROOT / "start.bat"
        vbs = folder / "NeonTerminal.vbs"
        text = 'CreateObject("WScript.Shell").Run """%s""", 7, False\r\n' % bat
        if folder.is_dir() and bat.exists() and (not vbs.exists() or vbs.read_text(errors="ignore") != text):
            vbs.write_text(text)
            print("已设置开机自动启动")
    except Exception:
        pass


class Server(ThreadingHTTPServer):
    """2026-11-21: on Windows SO_REUSEADDR let a restarted copy bind the same port next to a stuck old copy, and the old
    copy kept answering with old code for days. Windows gets an exclusive bind instead."""
    if os.name == "nt":
        allow_reuse_address = False

        def server_bind(self):
            import socket
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass
            super().server_bind()


def free_port(port):
    """Windows: end any other process still listening on our port (an old copy of this program), so new code takes over."""
    if os.name != "nt":
        return
    import subprocess
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
            pids.add(parts[4])
    pids.discard(str(os.getpid()))
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=15)
        print("stopped an old copy, pid", pid)
    if pids:
        time.sleep(2)


def main():
    autostart()
    threading.Thread(target=auto_update, daemon=True).start()
    port = int(os.environ.get("PORT", "8765"))
    host = os.environ.get("HOST", "127.0.0.1")  # the hosted copy listens behind Caddy on 127.0.0.1 too
    global SERVER
    free_port(port)
    for _ in range(60):   # after a restart the old copy may still hold the port for a moment
        try:
            server = Server((host, port), Handler)
            break
        except OSError:
            time.sleep(1)
    else:
        server = Server((host, port), Handler)
    SERVER = server
    print(f"ASX Neon Terminal 已启动: http://127.0.0.1:{port}  (关闭此窗口即停止)")
    if os.environ.get("NO_BROWSER") != "1":
        if not os.environ.get("ASX_DESK_RESTART"):
            webbrowser.open(f"http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
