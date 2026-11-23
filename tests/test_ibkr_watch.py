"""python tests/test_ibkr_watch.py -- 盈透后台用假行情完整跑一遍：到价提醒（一天一次）、断线 10 分钟推送和恢复、
盘口快照（实测订阅上限、分批、加密、只增不删、缺拍记录）、实时盘口请求、使用记账、12 月 19 日小检验。"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import depth_log as DL  # noqa: E402
import ibkr_watch as W  # noqa: E402

KEY = "ab" * 32


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeFeed:
    """Plays IB Gateway: quotes from a dict, at most `limit` depth books at once (the rest get 309), can be 'down'."""

    def __init__(self, limit=3):
        self.up = True
        self.limit = limit
        self.px = {}
        self.port = 4001
        self.tickers = {}
        self.batches = []

    def connected(self):
        return self.up

    def connect(self):
        return self.up

    def sleep(self, s):
        pass

    def quotes(self, codes):
        self.tickers = {c: 1 for c in codes}
        return {c: self.px.get(c, {}) for c in codes}

    def depth_batch(self, codes, wait=4.0):
        self.batches.append(list(codes))
        books, errs = {}, {}
        for i, c in enumerate(codes):
            if i >= self.limit:
                errs[c] = "309"
                continue
            base = 1.0 + (hash(c) % 50) / 100
            books[c] = ([(round(base - 0.01 * k, 3), 1000 * (k + 1)) for k in range(5)],
                        [(round(base + 0.01 * (k + 1), 3), 800 * (k + 1)) for k in range(5)])
        return books, errs


def main():
    tmp = Path(tempfile.mkdtemp())
    W.LOCAL, W.OUTDIR, W.DEPTH_DIR = tmp / "local", tmp / "out", tmp / "out" / "depth"
    pushes = []
    push = lambda title, msg, prio="high", tags="": pushes.append((title, msg)) or True  # noqa: E731

    # --- pure pieces ---
    r = DL.summarise_book([(1.00, 100), (0.99, 300)], [(1.02, 200)])
    assert r["bid_total"] == 400 and r["ask_total"] == 200 and r["ratio"] == 2.0 and abs(r["imb"] - 1 / 3) < 1e-3
    assert abs(r["spread"] - 0.02) < 1e-9 and abs(r["spread_pct"] - 1.9802) < 1e-3
    picked = DL.pick_codes([("提醒", ["CND"]), ("并购", ["SDF", "CND"]), ("爆拉", [f"X{i}" for i in range(100)])])
    assert len(picked) == 60 and picked[0] == ("CND", ["提醒", "并购"]) and picked[1][0] == "SDF"
    mon = datetime(2026, 11, 23, 10, 31)   # a Monday
    assert DL.due_slot(mon, set()) == "10:30" and DL.due_slot(mon, {"10:30"}) is None
    assert DL.due_slot(datetime(2026, 11, 22, 10, 31), set()) is None          # Sunday
    assert DL.missed_slots(datetime(2026, 11, 23, 10, 51), set()) == ["10:30"]

    # --- alerts: trigger once per condition per day ---
    W.add_alert("CND", up=0.0175)
    W.add_alert("BHP", down=50, pct=-3)
    try:
        W.add_alert("BHP")
        raise AssertionError("empty alert accepted")
    except ValueError:
        pass
    feed, clock = FakeFeed(), Clock(datetime(2026, 11, 24, 11, 0, 0))
    w = W.Watcher(feed=feed, clock=clock, list_source=lambda: [("并购", ["SDF"]), ("爆拉候选", None)], push=push, key=KEY)
    feed.px = {"CND": {"last": 0.017, "close": 0.017}, "BHP": {"last": 52.0, "close": 52.5}}
    w.tick()
    assert not pushes
    feed.px["CND"]["last"] = 0.018
    t0 = time.time()
    w.tick()
    assert len(pushes) == 1 and "CND" in pushes[0][0] and "0.018" in pushes[0][1] and time.time() - t0 < 10
    w.tick()
    assert len(pushes) == 1, "same condition pushed twice in a day"
    feed.px["BHP"] = {"last": 49.9, "close": 52.5}         # breaks 50 and is -4.95% -> two conditions
    w.tick()
    assert len(pushes) == 3, pushes
    clock.t = datetime(2026, 11, 25, 11, 0, 0)              # next day: may fire again
    w.tick()
    assert len(pushes) == 6, pushes
    clock.t = datetime(2026, 11, 25, 17, 0, 0)              # after the close: no checking
    feed.px["CND"]["last"] = 0.03
    w.tick()
    assert len(pushes) == 6
    usage = json.loads((W.OUTDIR / "ibkr_usage.json").read_text(encoding="utf-8"))
    assert len(usage) == 6 and usage[0]["purpose"] == "到价提醒"

    # --- outage: 10 minutes down in market hours -> one push, then 'restored' ---
    n0 = len(pushes)
    o = W.Outage(minutes=10)
    assert o.update(False, 0, True) is None and o.update(False, 599, True) is None
    assert o.update(False, 600, True) == "down" and o.update(False, 900, True) is None
    assert o.update(True, 901, True) == "restored" and o.update(True, 902, True) is None
    assert W.Outage().update(False, 0, False) is None and W.Outage().update(False, 10**6, False) is None
    w.outage = W.Outage(minutes=0)
    clock.t = datetime(2026, 11, 26, 11, 0, 0)
    feed.up = False
    w.tick()
    assert pushes[-1][0] == "盈透断线", pushes[-1]
    w.tick()
    assert len(pushes) == n0 + 1
    feed.up = True
    w.tick()
    assert pushes[n0 + 1][0] == "盈透已恢复", pushes[n0:]

    # --- snapshots: slot limit discovered, batches, encrypted, append-only, missing logged ---
    feed.batches.clear()
    clock.t = datetime(2026, 12, 1, 10, 30, 5)
    w.slots = None
    w.tick()
    day = "2026-12-01"
    raw = (W.DEPTH_DIR / f"{day}.json.enc").read_bytes()
    assert raw[:5] == b"ASXE1" and b"SDF" not in raw, "depth file not encrypted"
    d = DL.load_day(W.DEPTH_DIR, day, KEY)
    s = d["snapshots"][0]
    assert s["slot"] == "10:30" and w.slots == 3 and s["depth_slots"] == 3
    assert set(s["books"]) == {"CND", "BHP", "SDF"} and s["list_errors"] == {"爆拉候选": "取不到"}
    assert s["books"]["SDF"]["src"] == ["并购"] and s["books"]["CND"]["ratio"] is not None
    clock.t = datetime(2026, 12, 1, 13, 0, 30)
    w.tick()
    d = DL.load_day(W.DEPTH_DIR, day, KEY)
    assert [x["slot"] for x in d["snapshots"]] == ["10:30", "13:00"], "append-only broken"
    clock.t = datetime(2026, 12, 1, 16, 20, 0)             # 15:55 window passed without a tick -> logged as missing
    w.tick()
    miss = json.loads((W.DEPTH_DIR / "missing.json").read_text(encoding="utf-8"))
    assert miss[-1]["slot"] == "15:55" and miss[-1]["day"] == day
    # gateway down at slot time -> missing with reason
    feed.up = False
    clock.t = datetime(2026, 12, 2, 10, 55, 0)
    w.tick()
    miss = json.loads((W.DEPTH_DIR / "missing.json").read_text(encoding="utf-8"))
    assert miss[-1]["day"] == "2026-12-02" and "断线" in miss[-1]["reason"]
    feed.up = True
    # 60 codes, 3 slots -> 20 batches
    feed.batches.clear()
    w.list_source = lambda: [("爆拉候选", [f"A{i:02d}" for i in range(70)])]
    snap = w.snapshot("2026-12-02", "13:00")
    assert snap["asked"] == 60 and len(snap["books"]) == 60 and len(feed.batches) == 20 and max(map(len, feed.batches)) == 3
    w.slots, feed.limit = None, 4          # discover the limit on a full list
    snap = w.snapshot("2026-12-02", "15:55")
    assert w.slots == 4 and snap["batches"][0].get("probe") and len(snap["books"]) == 60, (w.slots, snap["batches"][:3])
    assert json.loads((W.LOCAL / "ibkr_settings.json").read_text(encoding="utf-8"))["depth_slots"] == 4
    # no key -> refuse to store
    try:
        DL.append_snapshot(tmp / "nokey", day, {"x": 1}, key=None) if not os.environ.get("ASX_DATA_KEY") else None
        assert os.environ.get("ASX_DATA_KEY"), "stored without a key"
    except RuntimeError:
        pass

    # --- live book request from the terminal (served inside the loop) ---
    import threading
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("r", w.request("depth", "CND", timeout=5)))
    th.start()
    time.sleep(0.2)
    w.tick()
    th.join()
    assert res["r"]["bid_total"] > 0 and len(res["r"]["bids"]) == 5

    # --- the 19 Dec test on made-up data where imbalance predicts tomorrow ---
    import random
    random.seed(1)
    days, bars = {}, {}
    dates = [(datetime(2026, 11, 24) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    dates = [x for x in dates if datetime.strptime(x, "%Y-%m-%d").weekday() < 5][:18]
    codes = [f"S{i:02d}" for i in range(40)]
    imbs = {(dd, c): random.uniform(-1, 1) for dd in dates for c in codes}
    for c in codes:
        px, rows = 1.0, []
        for k, dd in enumerate(dates):
            prev = imbs.get((dates[k - 1], c), 0) if k else 0
            o = px
            px = px * (1 + 0.03 * prev + random.gauss(0, 0.01))
            rows.append((dd, o, px))
        bars[c] = rows
    for dd in dates:
        days[dd] = {"snapshots": [{"slot": "15:55", "books": {c: {"imb": imbs[(dd, c)], "spread_pct": 0.5} for c in codes}}]}
    res = DL.analyse(days, bars)
    assert res["days"] >= 15 and res["cc"]["verdict"] == "初步有信号", res["cc"]
    for dd in dates:
        for c in codes:
            days[dd]["snapshots"][0]["books"][c]["imb"] = random.uniform(-1, 1)
    res2 = DL.analyse(days, bars)
    assert res2["cc"]["verdict"] == "没看到信号", res2["cc"]
    print("ok: alerts once/day, outage push + restore, snapshots (3 slots, 20 batches for 60, encrypted, append-only, "
          f"missing logged), live book, usage log, 19-Dec test ({res['cc']['t']} vs {res2['cc']['t']})")


if __name__ == "__main__":
    main()
