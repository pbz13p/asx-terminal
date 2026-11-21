"""Round 98: the "reversal ladder" seen on IVZ in 2020 -- does it work across all ASX small caps (delisted included)?

Frank 2026-11-21 asked how one could have spotted IVZ's turn from years of new lows without knowing the future.
Walking IVZ forward day by day gave five signals in Apr-Sep 2020, all before the 2021 run. One winner proves nothing,
so the same rules are tested on every ASX stock. Rules fixed 2026-11-21 before any result was seen:

  Setup ("long decline"): a close at the lowest close of the past 500 sessions, while price <= 50% of the 3-year high.
  The setup's low L = lowest low in the 60 sessions around it. An episode lasts 250 sessions from that low; within it
  each signal is counted once, the first time it happens:
    R1 spring      close >= 1.5 x L and above the close 20 sessions before the low day (broke down, then took it back)
    R2 above200    close above its 200-day simple average
    R3 stack       20 > 50 > 200-day averages and close above the 20-day
    R4 breakout    close above the highest high of the previous 250 sessions, with 20-day traded value >= 2x the 250-day
    R5 monthly     monthly MACD histogram (12/26/9 on month-end closes) turns from <= 0 to > 0 at a month end
  Ladder L1..L5 = the day the k-th different signal of the episode appears (signals stacking up).
  Entry: next session's open. Outcome within 252 sessions: reached 2x / 5x (any high), return at 252 sessions or at the
  last trade if the stock stopped trading (delisted stocks are in; their last price is used -- a real zero would be worse),
  worst drop before reaching 2x. Costs: round 71 (0.2% + one tick per side) on the 252-session return.
  Tradable: price >= 1c, 20-session median traded value >= A$20k (ASX volume only -- Cboe is missing, so this is strict).
  Baselines: (a) every tradable stock-day; (b) every tradable stock-day inside a setup episode with no signal yet.
  Periods: 2008-2018 vs 2019 on (outcomes need 252 sessions, so signals up to Nov 2025).
  Verdict per signal: 2x rate vs baseline (b), t of the difference with month clusters; "有效" only if t >= 3 in both
  periods with the same sign and >= 100 events in each; t 2-3 = 一般.
"""
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

import study
import study94 as L

START, SPLIT, END = "2008-01-01", "2019-01-01", "2025-11-21"
SIGS = ["R1", "R2", "R3", "R4", "R5"]


def sma(x, n):
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def roll_max(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def roll_min(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def monthly_macd_turns(d, c):
    """indices (last day of month) where the monthly MACD histogram turns from <= 0 to > 0."""
    ends = [i for i in range(len(d) - 1) if d[i][:7] != d[i + 1][:7]]
    if len(ends) < 30:
        return set()
    m = c[ends]
    def ema(a, n):
        k, e, out = 2 / (n + 1), a[0], []
        for v in a:
            e = v * k + e * (1 - k)
            out.append(e)
        return np.array(out)
    dif = ema(m, 12) - ema(m, 26)
    dea = ema(dif, 9)
    h = dif - dea
    return {ends[j] for j in range(27, len(h)) if h[j - 1] <= 0 < h[j]}


def outcome(o, h, c, raw, i):
    """entry at the open of session i; look 252 sessions ahead."""
    p = o[i]
    if not (p > 0):
        return None
    j_end = min(i + 252, len(c) - 1)
    seg_h = h[i:j_end + 1]
    x2 = bool((seg_h >= 2 * p).any())
    x5 = bool((seg_h >= 5 * p).any())
    k2 = int(np.argmax(seg_h >= 2 * p)) if x2 else len(seg_h)
    worst = float(np.min(c[i:i + k2 + 1]) / p - 1) if k2 >= 0 else 0.0
    ret = float(c[j_end] / p - 1 - 2 * L.side_cost(raw[i]))
    return {"x2": x2, "x5": x5, "ret": max(-1.0, min(ret, 50.0)), "worst": worst, "full": i + 252 < len(c)}


def main():
    t0 = time.time()
    ev = defaultdict(list)          # signal -> list of (date, outcome)
    base_all, base_setup = [], []
    rng = np.random.default_rng(98)
    n_stocks = 0
    for k, fp in enumerate(sorted(study.CACHE.glob("*.AU.json"))):
        rows = study.load(fp.name[:-5])
        if len(rows) < 800:
            continue
        d = [r[0] for r in rows]
        o = np.array([r[1] for r in rows]); h = np.array([r[2] for r in rows]); lo = np.array([r[3] for r in rows])
        c = np.array([r[4] for r in rows]); v = np.array([r[5] for r in rows]); raw = np.array([r[6] for r in rows])
        val = raw * v
        n_stocks += 1
        s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
        minc500 = roll_min(c, 500)
        maxh750 = roll_max(h, 750)
        maxh250_prev = np.concatenate([[np.nan], roll_max(h, 250)[:-1]])
        v20, v250 = sma(val, 20), sma(val, 250)
        from numpy.lib.stride_tricks import sliding_window_view
        med20 = np.full(len(c), np.nan)
        if len(c) >= 20:
            med20[19:] = np.median(sliding_window_view(val, 20), axis=1)
        mturn = monthly_macd_turns(d, c)
        tradable = (raw >= 0.01) & (med20 >= 20_000)
        ep_start, ep_low, ep_pre, seen = None, None, None, set()
        for i in range(750, len(c) - 1):
            if d[i] < START or d[i] > END:
                continue
            # setup: new 500-session closing low while <= 50% of the 3-year high
            if c[i] <= minc500[i] and c[i] <= 0.5 * maxh750[i]:
                lowday = i - int(np.argmin(lo[max(0, i - 59):i + 1][::-1]))
                ep_start, ep_low, seen = i, float(np.min(lo[max(0, i - 59):i + 1])), set()
                ep_pre = c[max(0, lowday - 20)]
            in_ep = ep_start is not None and i - ep_start <= 250
            if not in_ep:
                ep_start = None
            if tradable[i] and rng.random() < 0.05:   # baselines sampled (5% of days) to keep it fast
                out = outcome(o, h, c, raw, i + 1)
                if out:
                    base_all.append((d[i], out))
                    if in_ep and not seen:
                        base_setup.append((d[i], out))
            if not in_ep or not tradable[i]:
                continue
            fired = []
            if "R1" not in seen and c[i] >= 1.5 * ep_low and c[i] > ep_pre:
                fired.append("R1")
            if "R2" not in seen and s200[i] == s200[i] and c[i] > s200[i]:
                fired.append("R2")
            if "R3" not in seen and s200[i] == s200[i] and s20[i] > s50[i] > s200[i] and c[i] > s20[i]:
                fired.append("R3")
            if "R4" not in seen and maxh250_prev[i] == maxh250_prev[i] and c[i] > maxh250_prev[i] and v250[i] > 0 and v20[i] >= 2 * v250[i]:
                fired.append("R4")
            if "R5" not in seen and i in mturn:
                fired.append("R5")
            if not fired:
                continue
            out = outcome(o, h, c, raw, i + 1)
            if not out:
                continue
            before = len(seen)
            for s in fired:
                seen.add(s)
                ev[s].append((d[i], out))
            for kk in range(before + 1, len(seen) + 1):
                ev[f"L{kk}"].append((d[i], out))
        if k % 500 == 0:
            print(f"[98] {k} files", flush=True)

    def summ(lst):
        if not lst:
            return {"n": 0}
        x2 = np.mean([o["x2"] for _, o in lst]); x5 = np.mean([o["x5"] for _, o in lst])
        r = np.array([o["ret"] for _, o in lst])
        w = [o["worst"] for _, o in lst if o["x2"]]
        return {"n": len(lst), "x2_pct": round(100 * x2, 1), "x5_pct": round(100 * x5, 1), "mean_ret_pct": round(100 * r.mean(), 1),
                "median_ret_pct": round(100 * np.median(r), 1), "hit_pct": round(100 * np.mean(r > 0), 1),
                "worst_before_2x_median_pct": round(100 * np.median(w), 1) if w else None}

    def tdiff(a, b, key="x2"):
        def cl(lst):
            by = defaultdict(list)
            for dd, o in lst:
                by[dd[:7]].append(float(o[key]))
            return np.array([np.mean(v) for v in by.values()])
        A, B = cl(a), cl(b)
        if len(A) < 3 or len(B) < 3:
            return None
        se = np.sqrt(A.var(ddof=1) / len(A) + B.var(ddof=1) / len(B))
        return round(float((A.mean() - B.mean()) / se), 2) if se > 0 else None

    periods = {"all": lambda dd: True, "2008_2018": lambda dd: dd < SPLIT, "2019_on": lambda dd: dd >= SPLIT}
    out = {"round": 98, "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Rules fixed")[1][:2600], "stocks": n_stocks,
           "baseline_all": {}, "baseline_setup": {}, "signals": {}}
    for pn, pf in periods.items():
        ba = [x for x in base_all if pf(x[0])]; bs = [x for x in base_setup if pf(x[0])]
        out["baseline_all"][pn] = summ(ba); out["baseline_setup"][pn] = summ(bs)
        for s in SIGS + [f"L{k}" for k in range(1, 6)]:
            lst = [x for x in ev[s] if pf(x[0])]
            r = summ(lst)
            r["t_x2_vs_setup"] = tdiff(lst, bs, "x2")
            r["t_x5_vs_setup"] = tdiff(lst, bs, "x5")
            out["signals"].setdefault(s, {})[pn] = r
    verdict = {}
    for s in SIGS + [f"L{k}" for k in range(1, 6)]:
        a, b = out["signals"][s]["2008_2018"], out["signals"][s]["2019_on"]
        ta, tb = a.get("t_x2_vs_setup"), b.get("t_x2_vs_setup")
        if not ta or not tb or a.get("n", 0) < 100 or b.get("n", 0) < 100:
            verdict[s] = "不可信"
        elif ta >= 3 and tb >= 3:
            verdict[s] = "有效"
        elif ta >= 2 and tb >= 2:
            verdict[s] = "一般"
        else:
            verdict[s] = "无效"
    out["verdict"] = verdict
    out["seconds"] = round(time.time() - t0)
    p = Path(os.environ.get("STUDY98_OUT", "out/study98_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[98] done", out["seconds"], "s", flush=True)   # numbers stay in the encrypted file


if __name__ == "__main__":
    main()
