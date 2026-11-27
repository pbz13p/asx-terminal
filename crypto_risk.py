"""Liquidation / stop-out probability table for the crypto order preview (round 104, Frank 2026-11-27).

For BTC and ETH USDT perpetuals (Binance 1-hour klines from 2019-09, data/crypto, round 86 data) and every combination
  leverage 1/2/3/5 x stop 2%/5%/10%/none x hold 1/3/7/30 days x long/short,
an entry at the close of every hour (overlapping windows; the last 30 days are left out so every window is complete),
it records what happened first: the stop, a liquidation, or neither until the end of the hold.

Assumptions (fixed, the same as round 86 and the terminal's Hyperliquid ticket):
  * isolated margin; liquidation when the hourly low (long) / high (short) reaches
        entry x (1 -/+ (1/leverage - maintenance)),  maintenance = Hyperliquid's: BTC 1.25%, ETH 2% (half the
        initial margin at its max 40x / 25x). A liquidation loses the whole margin (-100%).
  * the stop is a stop-market order: filled at the stop price, or at the hour's open if the price gapped through it.
    If the stop and the liquidation level fall in the same hour, the one nearer the entry happens first.
  * costs: taker 0.05% + slippage 0.02% per side on the notional; funding actually paid/received over the hold.
  * results are % of the margin put up (what the trade can lose), so -100% = margin gone.
Output: data/crypto_risk.json (and out/crypto_risk.json when CRYPTO_RISK_OUT is set) -- encrypted before it leaves
the GitHub runner; the terminal reads it from the quick-results branch.
"""
import calendar
import json
import os
import time
from pathlib import Path

import numpy as np

DATA = Path("data/crypto")
SYMS = ("BTCUSDT", "ETHUSDT")
MM = {"BTCUSDT": 0.0125, "ETHUSDT": 0.02}
COST = 0.0005 + 0.0002          # per side, fraction of notional
LEVS = (1, 2, 3, 5)
STOPS = (0.02, 0.05, 0.10, None)
HOLDS = {1: 24, 3: 72, 7: 168, 30: 720}
START = "2019-09-01"


def load(sym):
    K = json.loads((DATA / f"{sym}_1h.json").read_text())
    F = json.loads((DATA / f"{sym}_funding.json").read_text())
    t = np.array([r[0] for r in K], dtype=np.int64)
    o, h, l, c = (np.array([r[k] for r in K], dtype=float) for k in (1, 2, 3, 4))
    fund = np.zeros(len(t))
    idx = np.searchsorted(t, np.array([x[0] for x in F], dtype=np.int64) - 1800)   # funding print -> the hour it falls in
    for j, (ft, rate) in zip(idx, F):
        if 0 <= j < len(t):
            fund[j] += rate
    return t, o, h, l, c, np.concatenate([[0.0], np.cumsum(fund)])   # cum[k] = funding printed in hours < k


def first_hits(o, l, h, c, side, dists, horizon):
    """For each entry i (close of hour i): first k in 1..horizon where the move against the position reaches each
    distance; also the fill factor (price / entry) at that bar for a stop-market order."""
    n = len(c) - horizon
    hit = {d: np.full(n, 10 ** 9, dtype=np.int64) for d in dists}
    fill = {d: np.zeros(n) for d in dists}
    base = c[:n]
    for k in range(1, horizon + 1):
        if side == "long":
            adverse = l[k:k + n] / base
            opn = o[k:k + n] / base
        else:
            adverse = h[k:k + n] / base
            opn = o[k:k + n] / base
        for d in dists:
            new = (hit[d] > 10 ** 8) & ((adverse <= 1 - d) if side == "long" else (adverse >= 1 + d))
            if new.any():
                hit[d][new] = k
                fill[d][new] = np.minimum(1 - d, opn[new]) if side == "long" else np.maximum(1 + d, opn[new])
    return hit, fill, n


def table(sym):
    t, o, h, l, c, cum = load(sym)
    first = int(np.searchsorted(t, calendar.timegm(time.strptime(START, "%Y-%m-%d"))))
    o, h, l, c, t = o[first:], h[first:], l[first:], c[first:], t[first:]
    cum = cum[first:] - cum[first]
    mm = MM[sym]
    hmax = max(HOLDS.values())
    out = {}
    for side in ("long", "short"):
        sgn = 1 if side == "long" else -1
        liqd = {L: 1 / L - mm for L in LEVS}
        dists = sorted({s for s in STOPS if s} | set(liqd.values()))
        hit, fill, n = first_hits(o, l, h, c, side, dists, hmax)
        idx = np.arange(n)
        for days, H in HOLDS.items():
            ret_end = c[H:H + n] / c[:n] - 1
            fund_end = cum[idx + H + 1] - cum[idx + 1]          # funding printed during hours i+1 .. i+H
            for L in LEVS:
                tl = hit[liqd[L]]
                for s in STOPS:
                    ts = hit[s] if s else np.full(n, 10 ** 9, dtype=np.int64)
                    stopped = (ts <= H) & ((ts < tl) | ((ts == tl) & (s is not None) & ((s or 1) < liqd[L])))
                    liq = ~stopped & (tl <= H)
                    pnl = L * (sgn * ret_end - sgn * fund_end) - L * 2 * COST
                    if s:
                        fs = fill[s]
                        fund_s = cum[idx + np.minimum(ts, H) + 1] - cum[idx + 1]
                        pnl_s = L * (sgn * (fs - 1) - sgn * fund_s) - L * 2 * COST
                        pnl = np.where(stopped, pnl_s, pnl)
                    pnl = np.where(liq, -1.0, np.maximum(pnl, -1.0))
                    out[f"{side}|{L}|{int(s * 100) if s else 0}|{days}"] = {
                        "n": int(n), "stop": round(float(stopped.mean()) * 100, 2), "liq": round(float(liq.mean()) * 100, 2),
                        "win": round(float((pnl > 0).mean()) * 100, 1), "mean": round(float(pnl.mean()) * 100, 2),
                        "median": round(float(np.median(pnl)) * 100, 2), "p05": round(float(np.percentile(pnl, 5)) * 100, 1),
                        "worst": round(float(pnl.min()) * 100, 1), "best": round(float(pnl.max()) * 100, 1),
                        "worst_when": time.strftime("%Y-%m-%d", time.gmtime(int(t[int(np.argmin(pnl))])))}
    return out, {"from": time.strftime("%Y-%m-%d", time.gmtime(int(t[0]))), "to": time.strftime("%Y-%m-%d", time.gmtime(int(t[-1]))),
                 "hours": int(len(t)), "mm": mm}


def build():
    res = {"made": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "costs_per_side": COST, "levs": LEVS,
           "stops": [int(s * 100) if s else 0 for s in STOPS], "holds": list(HOLDS), "key": "side|lev|stop%|days (stop 0 = none)",
           "note": "按 2019 年起每小时进场一次的历史（窗口重叠）；结果是占用保证金的百分比；-100% = 保证金全没",
           "symbols": {}}
    for sym in SYMS:
        if not (DATA / f"{sym}_1h.json").exists():
            continue
        tab, meta = table(sym)
        res["symbols"][sym] = {"meta": meta, "table": tab}
    return res


if __name__ == "__main__":
    r = build()
    Path("data").mkdir(exist_ok=True)
    Path("data/crypto_risk.json").write_text(json.dumps(r, ensure_ascii=False))
    if os.environ.get("CRYPTO_RISK_OUT"):
        Path(os.environ["CRYPTO_RISK_OUT"]).write_text(json.dumps(r, ensure_ascii=False))
    print("crypto risk table:", {k: len(v["table"]) for k, v in r["symbols"].items()})
