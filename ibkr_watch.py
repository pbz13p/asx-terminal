"""盈透后台：到价提醒、断线提醒、盘口快照、实时盘口（Frank 2026-11-24；规则见 depth_log.py 文件头）。

只在 Frank 自己的电脑上跑（server.py 启动时开一个后台线程；网上那份不跑）。只读 API，绝不下单：
连接时 readonly=True，Gateway 里也勾了 Read-Only API。

到价提醒
- 每条提醒：股票代码 + 向上突破价 / 向下跌破价 / 单日涨跌幅（%，正数 = 涨到、负数 = 跌到），三项可只填一项。
- 交易时段（周一到周五 10:00–16:12）用盈透实时报价（流式，每秒看一次）判断：最新价 ≥ 突破价、≤ 跌破价，
  或 最新价 / 昨收 − 1 达到设定的涨跌幅。触发后 ntfy 推到手机：代码、现价、触发条件、时间。
- 同一条提醒的同一个条件一天只推一次（记在 local/ibkr_fired.json）。
断线
- 连不上 Gateway、或 Gateway 报"和盈透服务器断开"（1100），都算断线。每 30 秒重连一次。
- 交易时段内（09:50–16:15）连续断线超过 10 分钟推一句「盈透断线」；恢复后推一句「盈透已恢复」。
  Frank 手机登录盈透会把 Gateway 踢掉，这是最常见的原因。
存放（都在 Frank 电脑的程序文件夹，不进仓库）
- local/ibkr_settings.json：ntfy 频道名（终端「⚙ 设置」里填一次）。
- local/ibkr_alerts.json：提醒列表；local/ibkr_fired.json：今天已推过的。
- out/ibkr_usage.json：使用记账（时间、股票、用途），只增不删。
- out/depth/：盘口快照（加密），见 depth_log.py。
"""
import json
import os
import queue
import threading
import time
import urllib.request
from concurrent.futures import Future
from pathlib import Path

import depth_log as DL

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / "local"
OUTDIR = ROOT / "out"
DEPTH_DIR = OUTDIR / "depth"
HOST, PORTS = "127.0.0.1", (4001, 4002)
CLIENT_ID = 41
OUTAGE_MIN = 10
_lock = threading.Lock()


# ---- small files --------------------------------------------------------------------------------------------------

def _read(f, default):
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(f, data):
    Path(f).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(f) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def settings():
    return _read(LOCAL / "ibkr_settings.json", {})


def save_settings(**kw):
    with _lock:
        s = settings()
        s.update({k: v for k, v in kw.items() if v is not None})
        _write(LOCAL / "ibkr_settings.json", s)
        return s


def alerts():
    return _read(LOCAL / "ibkr_alerts.json", [])


def add_alert(code, up=None, down=None, pct=None):
    code = str(code).upper().strip()
    if not code.isalnum() or len(code) > 6:
        raise ValueError("股票代码不正确")
    vals = {}
    for k, v in (("up", up), ("down", down), ("pct", pct)):
        if v not in (None, ""):
            v = float(v)
            if k != "pct" and v <= 0:
                raise ValueError("价格要大于 0")
            if k == "pct" and v == 0:
                raise ValueError("涨跌幅不能是 0")
            vals[k] = v
    if not vals:
        raise ValueError("至少填一项：突破价、跌破价或涨跌幅")
    with _lock:
        lst = alerts()
        a = {"id": f"{code}-{int(time.time() * 1000) % 10**9}", "code": code, "created": time.strftime("%Y-%m-%d %H:%M"), **vals}
        lst.append(a)
        _write(LOCAL / "ibkr_alerts.json", lst)
    return a


def del_alert(aid):
    with _lock:
        lst = [a for a in alerts() if a.get("id") != aid]
        _write(LOCAL / "ibkr_alerts.json", lst)
    return lst


def log_usage(code, purpose, note=""):
    """out/ibkr_usage.json: one row per use (alert fired, live book opened). Append-only."""
    with _lock:
        f = OUTDIR / "ibkr_usage.json"
        rows = _read(f, [])
        rows.append({"time": DL.melb_now().strftime("%Y-%m-%d %H:%M:%S"), "code": code, "purpose": purpose, "note": note})
        _write(f, rows)


def ntfy(title, msg, priority="high", tags="chart_with_upwards_trend", topic=None):
    topic = (topic or settings().get("ntfy_topic") or "").strip()
    if not topic:
        return False
    import base64
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"), method="POST",
                                 headers={"Title": "=?UTF-8?B?" + base64.b64encode(title.encode()).decode() + "?=",
                                          "Priority": priority, "Tags": tags})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        print("[ibkr] ntfy failed", type(e).__name__)
        return False


# ---- pure logic (tested with fake quotes) -------------------------------------------------------------------------

def in_session(now):
    """Alert checking window: weekdays 10:00-16:12 (continuous trading + closing auction)."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 600 <= hm <= 16 * 60 + 12


def in_watch_window(now):
    """Outage-alarm window: weekdays 09:50-16:15."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 590 <= hm <= 16 * 60 + 15


def check_alerts(alert_list, quotes, fired, day):
    """alert_list: stored alerts; quotes: {code: {"last":, "close":}}; fired: {key: time} for today.
    Returns [(alert, cond_key, text)] newly triggered; adds them to fired."""
    hits = []
    for a in alert_list:
        q = quotes.get(a["code"]) or {}
        last, close = q.get("last"), q.get("close")
        if not last or last <= 0:
            continue
        conds = []
        if a.get("up") and last >= a["up"]:
            conds.append(("up", f"向上突破 {a['up']:g}"))
        if a.get("down") and last <= a["down"]:
            conds.append(("down", f"向下跌破 {a['down']:g}"))
        if a.get("pct") and close and close > 0:
            ch = 100 * (last / close - 1)
            if (a["pct"] > 0 and ch >= a["pct"]) or (a["pct"] < 0 and ch <= a["pct"]):
                conds.append(("pct", f"单日{'涨' if a['pct'] > 0 else '跌'}到 {ch:+.2f}%（设定 {a['pct']:+g}%）"))
        for k, text in conds:
            key = f"{day}|{a['id']}|{k}"
            if key in fired:
                continue
            fired[key] = time.strftime("%H:%M:%S")
            hits.append((a, k, text))
    return hits


class Outage:
    """Tracks how long the link has been down; says when to push 断线 / 恢复."""

    def __init__(self, minutes=OUTAGE_MIN):
        self.limit = minutes * 60
        self.since = None
        self.pushed = False

    def update(self, ok, now_ts, watch):
        if ok:
            was = self.pushed
            self.since, self.pushed = None, False
            return "restored" if was else None
        if self.since is None:
            self.since = now_ts
        if watch and not self.pushed and now_ts - self.since >= self.limit:
            self.pushed = True
            return "down"
        return None


# ---- the IB side ---------------------------------------------------------------------------------------------------

class IBFeed:
    """Real feed through IB Gateway (ib_async, sync API inside the watcher thread)."""

    def __init__(self):
        self.ib = None
        self.tickers = {}
        self.farm_ok = True
        self.errors = []
        self.port = None

    def _lib(self):
        import ibkr_link
        return ibkr_link._lib()

    def connected(self):
        return bool(self.ib and self.ib.isConnected() and self.farm_ok)

    def connect(self):
        import asyncio
        ib_async = self._lib()
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        if self.ib is None:
            self.ib = ib_async.IB()
            self.ib.errorEvent += self._on_error
        if self.ib.isConnected():          # socket up but Gateway lost IB's servers (1100): wait for 1101/1102
            return self.farm_ok
        for p in PORTS:
            try:
                self.ib.connect(HOST, p, clientId=CLIENT_ID, timeout=10, readonly=True)
                self.port, self.farm_ok, self.tickers = p, True, {}
                self.ib.reqMarketDataType(1)
                return True
            except Exception:
                try:
                    self.ib.disconnect()
                except Exception:
                    pass
        return False

    def _on_error(self, reqId, code, msg, *a):
        if code == 1100 or code == 2110:
            self.farm_ok = False
        elif code in (1101, 1102):
            self.farm_ok = True
        self.errors.append((time.time(), reqId, code, str(msg)[:160]))
        self.errors = self.errors[-200:]

    def sleep(self, s):
        if self.ib and self.ib.isConnected():
            self.ib.sleep(s)
        else:
            time.sleep(s)

    def _contract(self, code):
        ib_async = self._lib()
        c = ib_async.Stock(code, "ASX", "AUD")
        if not self.ib.qualifyContracts(c):
            return None
        return c

    def quotes(self, codes):
        """Keep streaming quotes for exactly these codes; return {code: {"last", "close", "bid", "ask"}}."""
        for code in list(self.tickers):
            if code not in codes:
                try:
                    self.ib.cancelMktData(self.tickers.pop(code).contract)
                except Exception:
                    pass
        for code in codes:
            if code not in self.tickers:
                c = self._contract(code)
                if c is not None:
                    self.tickers[code] = self.ib.reqMktData(c, "", False, False)
        out = {}
        ok = lambda x: x if x == x and x is not None and x > 0 else None   # NaN / -1 -> None
        for code, t in self.tickers.items():
            out[code] = {"last": ok(t.last), "close": ok(t.close), "bid": ok(t.bid), "ask": ok(t.ask)}
        return out

    def depth_batch(self, codes, wait=4.0):
        """Open depth for these codes at once, wait for the book, read 5 levels, cancel.
        Returns ({code: (bids, asks)}, {code: error}); error "309" = refused because too many books were open."""
        books, errs, subs = {}, {}, {}
        t0 = time.time()
        for code in codes:
            c = self._contract(code)
            if c is None:
                errs[code] = "盈透找不到"
                continue
            t = self.ib.reqMktDepth(c, numRows=DL.LEVELS, isSmartDepth=False)
            subs[code] = (c, t, self.ib.wrapper.ticker2ReqId["mktDepth"].get(t))
        end = time.time() + wait
        while time.time() < end:
            self.ib.sleep(0.25)
            if subs and time.time() - t0 > 1.5 and all(t.domBids and t.domAsks for _, t, _ in subs.values()):
                break
        refused = {e[1]: e[2] for e in self.errors if e[0] >= t0 and e[2] in (309, 310, 354, 10092, 200, 162)}
        for code, (c, t, rid) in subs.items():
            if rid in refused:
                errs[code] = "309" if refused[rid] == 309 else f"错误 {refused[rid]}"
            else:
                books[code] = ([(x.price, x.size) for x in t.domBids[:DL.LEVELS]], [(x.price, x.size) for x in t.domAsks[:DL.LEVELS]])
            try:
                self.ib.cancelMktDepth(c, isSmartDepth=False)
            except Exception:
                pass
        self.ib.sleep(0.3)
        return books, errs


class Watcher:
    def __init__(self, feed=None, clock=None, list_source=None, push=None, key=None):
        self.feed = feed or IBFeed()
        self.clock = clock or DL.melb_now
        self.list_source = list_source          # callable -> [(tag, [codes])] (alerts are added in front)
        self.push = push or ntfy
        self.key = key
        self.outage = Outage()
        self.fired = _read(LOCAL / "ibkr_fired.json", {})
        self.reqs = queue.Queue()
        self.status = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "connected": False, "pushes": 0}
        self.done = {}          # day -> set(slots)
        self.slots = settings().get("depth_slots")
        self._last_connect = 0

    # one tick of the loop (tests call this directly with a fake clock)
    def tick(self):
        now = self.clock()
        day = now.strftime("%Y-%m-%d")
        ok = self.feed.connected()
        if not ok and time.time() - self._last_connect >= 30:
            self._last_connect = time.time()
            ok = self.feed.connect()
        self.status.update(connected=ok, port=getattr(self.feed, "port", None), checked=now.strftime("%H:%M:%S"))
        ev = self.outage.update(ok, time.time(), in_watch_window(now))
        if ev == "down":
            self._push("盈透断线", f"IB Gateway 断开已超过 {OUTAGE_MIN} 分钟（{now:%H:%M}）。常见原因：手机或网页登录了盈透。"
                                  "退出手机上的盈透后，Gateway 会自己连回来；不行就在电脑上重新登录 Gateway。", "high", "warning")
        elif ev == "restored":
            self._push("盈透已恢复", f"{now:%H:%M} 已重新连上 IB Gateway。", "default", "white_check_mark")
        self._serve_requests(ok)
        if not ok:
            for s in DL.missed_slots(now, self.done.setdefault(day, set())):
                DL.log_missing(DEPTH_DIR, day, s, "盈透断线 / Gateway 没登录")
                self.done[day].add(s)
            return
        # price alerts
        self.check_prices(now, day)
        # depth snapshots
        slot = DL.due_slot(now, self.done.setdefault(day, set()))
        if slot:
            self.done[day].add(slot)
            try:
                self.snapshot(day, slot)
            except Exception as e:
                DL.log_missing(DEPTH_DIR, day, slot, f"出错：{type(e).__name__} {str(e)[:120]}")
        for s in DL.missed_slots(now, self.done[day]):
            DL.log_missing(DEPTH_DIR, day, s, "到点时终端或电脑没在跑")
            self.done[day].add(s)

    def check_prices(self, now, day):
        al = alerts()
        codes = sorted({a["code"] for a in al})
        if not codes or not in_session(now):
            if getattr(self.feed, "tickers", None):
                self.feed.quotes([])
            return
        fired_today = {k: v for k, v in self.fired.items() if k.startswith(day)}
        before = len(fired_today)
        quotes = self.feed.quotes(codes)
        self.status["quotes"] = {c: q.get("last") for c, q in quotes.items()}
        for a, k, text in check_alerts(al, quotes, fired_today, day):
            q = quotes.get(a["code"]) or {}
            self._push(f"{a['code']} {text}", f"{a['code']} 现价 {q.get('last'):g}，{text}。时间 {now:%H:%M:%S}（盈透实时价）", "high", "bell")
            log_usage(a["code"], "到价提醒", text)
        if len(fired_today) != before or len(fired_today) != len(self.fired):
            self.fired = fired_today
            _write(LOCAL / "ibkr_fired.json", self.fired)

    def _push(self, title, msg, prio="high", tags="bell"):
        if self.push(title, msg, prio, tags):
            self.status["pushes"] += 1
            self.status["last_push"] = time.strftime("%H:%M:%S") + " " + title

    def _serve_requests(self, ok):
        while True:
            try:
                kind, arg, fut = self.reqs.get_nowait()
            except queue.Empty:
                return
            try:
                if not ok:
                    raise RuntimeError("盈透没连上（Gateway 没开、没登录，或被手机登录踢掉了）")
                if kind == "depth":
                    books, errs = self.feed.depth_batch([arg], wait=3.0)
                    if arg in books:
                        fut.set_result(DL.summarise_book(*books[arg]))
                    else:
                        fut.set_result({"error": errs.get(arg, "没拿到盘口")})
                else:
                    fut.set_result(None)
            except Exception as e:
                fut.set_exception(e)

    def request(self, kind, arg, timeout=12):
        fut = Future()
        self.reqs.put((kind, arg, fut))
        return fut.result(timeout=timeout)

    def _detect_slots(self, codes):
        books, errs = self.feed.depth_batch(codes[:10], wait=4.0)
        n = len([c for c in codes[:10] if errs.get(c) != "309"])
        return max(1, n), books, errs

    def snapshot(self, day, slot):
        t0 = time.time()
        al = [("提醒", [a["code"] for a in alerts()])]
        list_errors = {}
        try:
            srcs = self.list_source() if self.list_source else []
        except Exception as e:
            srcs, list_errors["all"] = [], type(e).__name__
        for tag, codes in srcs:
            if codes is None:
                list_errors[tag] = "取不到"
        picked = DL.pick_codes(al + [(t, c) for t, c in srcs if c])
        codes = [c for c, _ in picked]
        books, errs, batches = {}, {}, []
        rest = list(codes)
        if not self.slots and rest:
            n, b, e = self._detect_slots(rest)
            self.slots = n
            if len(rest) >= 10 or any(v == "309" for v in e.values()):   # only a real test of the limit is remembered
                save_settings(depth_slots=n)
            first = rest[:10]
            batches.append({"codes": len(first), "sec": round(time.time() - t0, 1), "probe": True})
            books.update(b)
            errs.update({k: v for k, v in e.items() if v != "309"})
            rest = [c for c in rest if c not in b and c not in errs]
        while rest:
            part, rest = rest[:self.slots], rest[self.slots:]
            tb = time.time()
            b, e = self.feed.depth_batch(part)
            if any(v == "309" for v in e.values()) and self.slots > 1:   # limit got smaller (e.g. live book open): retry later
                self.slots = max(1, self.slots - 1)
                rest = [c for c in part if e.get(c) == "309"] + rest
            books.update(b)
            errs.update({k: v for k, v in e.items() if v != "309"})
            batches.append({"codes": len(part), "sec": round(time.time() - tb, 1)})
            now = self.clock()
            self.check_prices(now, now.strftime("%Y-%m-%d"))   # keep price alerts live while the snapshot runs
        tags = dict(picked)
        snap = {"slot": slot, "taken": self.clock().strftime("%H:%M:%S"), "secs": round(time.time() - t0, 1),
                "depth_slots": self.slots, "batches": batches, "list_errors": list_errors,
                "books": {c: dict(DL.summarise_book(*books[c]), src=tags.get(c, [])) for c in books},
                "errors": errs, "asked": len(codes)}
        n = DL.append_snapshot(DEPTH_DIR, day, snap, self.key)
        self.status["last_snapshot"] = f"{day} {slot}：{len(books)}/{len(codes)} 只，{snap['secs']} 秒，每批 {self.slots} 只（今天第 {n} 张）"
        return snap

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as e:
                self.status["last_error"] = f"{time.strftime('%H:%M:%S')} {type(e).__name__}: {str(e)[:160]}"
            try:
                self.feed.sleep(1)
            except Exception:
                time.sleep(1)


WATCHER = None


def default_lists():
    """[(tag, [codes] or None)] from the encrypted daily results (merger board, ladder, rockets)."""
    import server
    out = []
    for tag, name, pick in (("并购", "merger_board.json", lambda d: [x["code"] for x in d.get("deals", [])]),
                            ("底部放量突破", "ladder_lists.json", lambda d: [x["code"] for x in d.get("r4", [])]),
                            ("底部弹簧", "ladder_lists.json", lambda d: [x["code"] for x in d.get("r1", [])]),
                            ("爆拉候选", "study22_results.json", lambda d: [x["code"] for x in d.get("rockets", [])])):
        try:
            out.append((tag, pick(_cached_result(server, name))))
        except Exception:
            out.append((tag, None))
    return out


_rc = {}


def _cached_result(server, name):
    hit = _rc.get(name)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    d = server.fetch_result(name)
    _rc[name] = (time.time(), d)
    return d


def start():
    """Called once by server.py on Frank's PC."""
    global WATCHER
    if WATCHER is not None:
        return WATCHER
    WATCHER = Watcher(list_source=default_lists)

    def go():
        import asyncio
        asyncio.set_event_loop(asyncio.new_event_loop())
        WATCHER.run()
    threading.Thread(target=go, daemon=True, name="ibkr-watch").start()
    return WATCHER
