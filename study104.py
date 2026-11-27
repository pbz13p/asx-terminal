"""Round 104: funding-rate extremes on BTC / ETH perpetuals -- fade the crowd? (Frank 2026-11-27, before real money on
Hyperliquid, F8). Rules fixed HERE, before any result was seen; committed to git before the first run.

Data (round 86, Binance public bulk files): data/crypto/{BTC,ETH}USDT_1h.json, _funding.json (every 8 h),
_metrics.json (open interest, hourly, from 2021-12; crypto_download.py).

EVENTS
  E1 funding only: at each funding print, its rank among the prints of the previous 30 days (needs >= 60 of them).
       rank >= 95%  -> longs crowded  -> SHORT
       rank <=  5%  -> shorts crowded -> LONG
     De-clustering: an event counts only if the last counted event of the same coin and side is >= 3 days earlier.
  E2 = E1 and open interest at the print up >= 20% over the previous 7 days (only where metrics exist, 2021-12 on).
TRADE
  Enter at the open of the first hourly bar AFTER the print; exit at the open H hours later, H = 24 / 72 / 168
  (1 / 3 / 7 days). No other parameters, nothing tuned.
  Leverage 1x / 3x / 5x, isolated: liquidated if an hourly low (long) / high (short) reaches
  entry x (1 -/+ (1/L - maintenance)); maintenance = Hyperliquid's (BTC 1.25%, ETH 2%); liquidation = -100% of margin.
  Costs (round 86): taker 0.05% + slippage 0.02% per side on the notional; funding actually paid / received over the hold.
  Result per trade = % of margin.
SAMPLES  2019-09 .. 2023-12 = rule-setting (in-sample); 2024-01 on = out-of-sample.
MAIN (the only line the verdict uses): E1, both coins, both sides, hold 3 days, 1x, out-of-sample.
  有效 needs all three: mean > 0 after costs; t > 2 (clustered by entry day); real mean beats >= 95% of 200 shuffles
  (each shuffle: same coins, sides and counts, event times replaced by random funding prints of the same period).
  一般 = mean > 0 but a test fails. 无效 = mean <= 0. Holds 1 / 7 days, E2, 3x / 5x are reported, never used to rescue.
  Trials registered: 2 events x 3 holds x 3 leverages = 18 (research/trials.json).
  Only 有效 may become a phone signal (ntfy). Otherwise ROADMAP says 不成立 and no signal is added.
HYPERLIQUID CHECK (public info API, fundingHistory, no key): the last 30 days of BTC and ETH; hourly rates summed
  over each Binance 8-hour period, compared with Binance's print: same sign share and correlation.
  Same sign < 70% -> the result is written as "only for Binance".
ALSO (done whatever the verdict): crypto_risk.py liquidation / stop-out table (2019 on) and crypto_risk_check.py,
  an independent re-computation of 3 of its cells. Both written to out/ and encrypted.
"""
import calendar
import json
import math
import os
import random
import time
import urllib.request
from pathlib import Path

import numpy as np

import crypto_risk
from validate import clustered_t

DATA = Path("data/crypto")
SYMS = ("BTCUSDT", "ETHUSDT")
MM = {"BTCUSDT": 0.0125, "ETHUSDT": 0.02}
COST = 0.0007
HOLDS = (24, 72, 168)
LEVS = (1, 3, 5)
SPLIT = calendar.timegm(time.strptime("2024-01-01", "%Y-%m-%d"))
DAY = 86400


def load(sym):
    K = json.loads((DATA / f"{sym}_1h.json").read_text())
    F = json.loads((DATA / f"{sym}_funding.json").read_text())
    M = json.loads((DATA / f"{sym}_metrics.json").read_text()) if (DATA / f"{sym}_metrics.json").exists() else []
    t = np.array([r[0] for r in K], dtype=np.int64)
    o, h, l = (np.array([r[k] for r in K], dtype=float) for k in (1, 2, 3))
    F = [(int(a), float(b)) for a, b in F]
    return t, o, h, l, F, {int(r[0]): r[1] for r in M if r[1]}


_FC = {}


def fund_between(F, a, b):
    """Sum of funding prints with a <= time < b (cumulative sums, cached per series)."""
    key = id(F)
    if key not in _FC:
        _FC[key] = (np.array([x[0] for x in F], dtype=np.int64), np.concatenate([[0.0], np.cumsum([x[1] for x in F])]))
    ts, cs = _FC[key]
    return float(cs[np.searchsorted(ts, b)] - cs[np.searchsorted(ts, a)])


def events(F, oi):
    out, last = [], {}
    times = np.array([x[0] for x in F])
    rates = np.array([x[1] for x in F])
    for j, (ft, r) in enumerate(F):
        lo = np.searchsorted(times, ft - 30 * DAY)
        win = rates[lo:j]
        if len(win) < 60:
            continue
        rank = float((win < r).mean() + 0.5 * (win == r).mean())
        side = "short" if rank >= 0.95 else "long" if rank <= 0.05 else None
        if not side:
            continue
        if side in last and ft - last[side] < 3 * DAY:
            continue
        last[side] = ft
        hr = (ft // 3600) * 3600
        o_now, o_old = oi.get(hr), oi.get(hr - 7 * DAY)
        oi_up = (o_now / o_old - 1) if (o_now and o_old) else None
        out.append({"t": ft, "side": side, "rate": r, "rank": round(rank, 3), "oi_up": oi_up})
    return out


def trade(t, o, h, l, F, ft, side, H, L, mm):
    i = int(np.searchsorted(t, ft, side="right"))          # first bar starting after the print
    if i + H >= len(t):
        return None
    e, x = o[i], o[i + H]
    sgn = 1 if side == "long" else -1
    liq_px = e * (1 - (1 / L - mm)) if side == "long" else e * (1 + (1 / L - mm))
    if L > 1 and ((l[i:i + H].min() <= liq_px) if side == "long" else (h[i:i + H].max() >= liq_px)):
        return -1.0, t[i]
    fund = fund_between(F, t[i], t[i + H])
    return max(-1.0, L * (sgn * (x / e - 1) - sgn * fund) - L * 2 * COST), t[i]


def stats(rows):
    if not rows:
        return {"n": 0}
    v = np.array([r[0] for r in rows])
    days = [int(r[1] // DAY) for r in rows]
    wins, losses = v[v > 0], v[v <= 0]
    streak = best = 0
    for x in v:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    return {"n": len(v), "mean": round(float(v.mean()) * 100, 3), "median": round(float(np.median(v)) * 100, 3),
            "win": round(float((v > 0).mean()) * 100, 1), "avg_win": round(float(wins.mean()) * 100, 2) if len(wins) else None,
            "avg_loss": round(float(losses.mean()) * 100, 2) if len(losses) else None, "t": clustered_t(list(v), days),
            "worst": round(float(v.min()) * 100, 1), "liquidated": int((v <= -0.999).sum()), "longest_losing_run": best}


def hl_check(F_by):
    res = {}
    for sym in SYMS:
        coin = sym.replace("USDT", "")
        F = F_by[sym]
        end = F[-1][0] * 1000
        start = end - 30 * DAY * 1000
        rows, cur = [], start
        try:
            while cur < end:
                body = json.dumps({"type": "fundingHistory", "coin": coin, "startTime": int(cur), "endTime": int(end)}).encode()
                req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    part = json.load(r)
                if not part:
                    break
                rows += part
                nxt = max(int(x["time"]) for x in part) + 1
                if nxt <= cur:
                    break
                cur = nxt
                time.sleep(0.3)
        except Exception as e:
            res[sym] = {"error": f"{type(e).__name__} {e}"[:160]}
            continue
        hl = sorted({int(x["time"]) // 1000: float(x["fundingRate"]) for x in rows}.items())
        pairs = []
        for ft, br in F:
            if ft * 1000 < start:
                continue
            s = [r for ht, r in hl if ft - 8 * 3600 < ht <= ft + 60]
            if len(s) >= 6:
                pairs.append((br, sum(s) * 8 / len(s)))
        if len(pairs) < 20:
            res[sym] = {"error": f"only {len(pairs)} matched periods", "hl_points": len(hl)}
            continue
        a, b = np.array(pairs).T
        res[sym] = {"periods": len(pairs), "same_sign_pct": round(float((np.sign(a) == np.sign(b)).mean()) * 100, 1),
                    "corr": round(float(np.corrcoef(a, b)[0, 1]), 3), "binance_mean_8h_pct": round(float(a.mean()) * 100, 4),
                    "hyperliquid_mean_8h_pct": round(float(b.mean()) * 100, 4)}
    ok = [v.get("same_sign_pct", 0) for v in res.values() if "same_sign_pct" in v]
    res["verdict"] = ("同向，结论可以参考到 Hyperliquid" if ok and min(ok) >= 70 else
                      "差太多或没取到：结论只适用于 Binance")
    return res


def main():
    out_path = os.environ.get("STUDY104_OUT", "out/study104_results.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(104)
    D, F_by, EV = {}, {}, {}
    for sym in SYMS:
        t, o, h, l, F, oi = load(sym)
        D[sym], F_by[sym] = (t, o, h, l, F), F
        EV[sym] = events(F, oi)
    res = {"round": 104, "events": {s: {"E1": len(EV[s]), "E2": sum(1 for e in EV[s] if (e["oi_up"] or 0) >= 0.20),
                                        "with_oi": sum(1 for e in EV[s] if e["oi_up"] is not None),
                                        "short_events": sum(1 for e in EV[s] if e["side"] == "short")} for s in SYMS},
           "grid": {}}
    trades = {}
    for var in ("E1", "E2"):
        for H in HOLDS:
            for L in LEVS:
                for per in ("in", "out"):
                    rows = []
                    for sym in SYMS:
                        t, o, h, l, F = D[sym]
                        for e in EV[sym]:
                            if var == "E2" and not ((e["oi_up"] or 0) >= 0.20):
                                continue
                            if (e["t"] >= SPLIT) != (per == "out"):
                                continue
                            r = trade(t, o, h, l, F, e["t"], e["side"], H, L, MM[sym])
                            if r:
                                rows.append(r)
                    rows.sort(key=lambda r: r[1])
                    key = f"{var}|{H // 24}d|{L}x|{per}"
                    trades[key] = rows
                    res["grid"][key] = stats(rows)
    # by side, main config
    for side in ("long", "short"):
        for per in ("in", "out"):
            rows = []
            for sym in SYMS:
                t, o, h, l, F = D[sym]
                for e in EV[sym]:
                    if e["side"] == side and (e["t"] >= SPLIT) == (per == "out"):
                        r = trade(t, o, h, l, F, e["t"], side, 72, 1, MM[sym])
                        if r:
                            rows.append(r)
            res["grid"][f"E1|3d|1x|{per}|{side}"] = stats(rows)
    # shuffles (out-of-sample, 1x, every hold, E1)
    res["shuffle"] = {}
    for H in HOLDS:
        real = res["grid"][f"E1|{H // 24}d|1x|out"].get("mean")
        if real is None:
            continue
        means = []
        pools = {}
        for sym in SYMS:
            t = D[sym][0]
            pools[sym] = [ft for ft, _ in D[sym][4] if ft >= SPLIT and np.searchsorted(t, ft, side="right") + H < len(t)]
        for _ in range(200):
            v = []
            for sym in SYMS:
                t, o, h, l, F = D[sym]
                pool = pools[sym]
                for e in EV[sym]:
                    if e["t"] >= SPLIT:
                        r = trade(t, o, h, l, F, rng.choice(pool), e["side"], H, 1, MM[sym])
                        if r:
                            v.append(r[0])
            means.append(np.mean(v) * 100 if v else 0)
        means = np.array(means)
        res["shuffle"][f"{H // 24}d"] = {"real_mean": real, "shuffle_median": round(float(np.median(means)), 3),
                                        "share_of_shuffles_at_or_above_real": round(float((means >= real).mean()), 3),
                                        "rank_pct": round(float((means < real).mean()) * 100, 1)}
    m = res["grid"]["E1|3d|1x|out"]
    sh = res["shuffle"].get("3d", {})
    passed = [bool(m.get("mean") is not None and m["mean"] > 0), bool(m.get("t") and m["t"] > 2),
              bool(sh and sh["share_of_shuffles_at_or_above_real"] <= 0.05)]
    res["main"] = {"config": "E1, BTC+ETH, both sides, hold 3 days, 1x, out-of-sample 2024+", **m, "shuffle": sh,
                   "tests": {"mean_positive": passed[0], "t_above_2": passed[1], "beats_95pct_shuffles": passed[2]},
                   "verdict": "有效" if all(passed) else ("一般" if passed[0] else "无效")}
    res["recent_events"] = [{"sym": s, "time": time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["t"])), "side": e["side"],
                             "rate_pct": round(e["rate"] * 100, 4), "rank": e["rank"], "oi_up": e["oi_up"]}
                            for s in SYMS for e in EV[s][-5:]]
    res["hyperliquid_check"] = hl_check(F_by)
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False))
    print(json.dumps(res["main"], ensure_ascii=False), flush=True)
    # liquidation table + independent check (acceptance)
    rt = crypto_risk.build()
    Path("data/crypto_risk.json").write_text(json.dumps(rt, ensure_ascii=False))
    Path(Path(out_path).parent / "crypto_risk.json").write_text(json.dumps(rt, ensure_ascii=False))
    import crypto_risk_check as C
    tab = rt["symbols"]
    chk = []
    for sym, side, L, sp, d in C.CELLS:
        mine = C.one(sym, side, L, sp, d)
        theirs = tab[sym]["table"][f"{side}|{L}|{sp}|{d}"]
        chk.append({"cell": f"{sym} {side} {L}x stop {sp}% {d}d", "independent": mine,
                    "table": {k: theirs[k] for k in ("n", "stop", "liq", "mean")},
                    "match": mine["n"] == theirs["n"] and all(abs(mine[k] - theirs[k]) <= 0.01 for k in ("stop", "liq", "mean"))})
    res["risk_table_check"] = chk
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False))
    print("risk check:", [c["match"] for c in chk], flush=True)


if __name__ == "__main__":
    main()
