"""Round 105: re-test round 98's "reversal ladder" on US stocks (Frank 2026-11-27: "美股下完之后你就去复测").

Why: round 98 found R4 (long decline, then a close above the 1-year high on 2x traded value) lifted the 1-year 2x rate
on ASX small caps (t 3.3 / 3.7). Round 103 then found the holding version did not hold up. A market ~10x bigger with
survivorship-free data (us_history.py, delisted included) tells us whether R4 is a real pattern or an ASX accident.

Rules = round 98's, copied unchanged (fixed 2026-11-21, header of study98.py), only the market-specific parts changed,
and those were fixed HERE on 2026-11-27 before any US result was seen:
  Universe   every US common stock in data/us_hist (NYSE / NASDAQ / AMEX / ARCA / BATS), delisted included; ETFs out.
  Prices     O/H/L/C scaled by adjusted_close / close (splits, dividends); raw close for price and traded value.
  Tradable   raw price >= US$1 and 20-session median traded value >= US$100k (a US$5k position is <= 5% of a day).
  Costs      per side 0.1% + one cent / price (IBKR commission + half a typical spread) on the 252-session return.
  Setup / R1..R5 / ladder L1..L5 / outcome (2x, 5x within 252 sessions, return, worst drop before 2x) / baselines
             (a) every tradable stock-day, (b) tradable stock-days inside a setup episode with no signal yet (5% sample)
             -- exactly as round 98.
  Periods    2008-2018 vs 2019 on (signals up to 2025-11-21 so every outcome has 252 sessions).
  Verdict    per signal: 2x rate vs baseline (b), t of the difference with month clusters; 有效 only if t >= 3 in BOTH
             periods with the same sign and >= 100 events in each; t 2-3 = 一般; else 无效.
  Main line  R4 (the only round-98 signal that passed). It "replicates" only if R4 is 有效 here too.
Trials registered: 5 signals + 5 ladder steps = 10 (same grid as round 98; research/trials.json).
Also reported: 1-year 10x rate of all US stocks vs ASX (for round 80 / 82 context).
"""
import gzip
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

import study98 as R

DATA = Path("data/us_hist")
START, SPLIT, END = "2008-01-01", "2019-01-01", "2025-11-21"
SIGS = R.SIGS


def side_cost(raw):
    return 0.001 + 0.01 / raw if raw > 0 else 0.05


def outcome(o, h, c, raw, i):
    p = o[i]
    if not (p > 0):
        return None
    j_end = min(i + 252, len(c) - 1)
    seg_h = h[i:j_end + 1]
    x2 = bool((seg_h >= 2 * p).any())
    x5 = bool((seg_h >= 5 * p).any())
    x10 = bool((seg_h >= 10 * p).any())
    k2 = int(np.argmax(seg_h >= 2 * p)) if x2 else len(seg_h)
    worst = float(np.min(c[i:i + k2 + 1]) / p - 1)
    ret = float(c[j_end] / p - 1 - 2 * side_cost(raw[i]))
    return {"x2": x2, "x5": x5, "x10": x10, "ret": max(-1.0, min(ret, 50.0)), "worst": worst}


def load(code):
    rows = json.loads(gzip.decompress((DATA / f"{code}.json.gz").read_bytes()))
    rows = [r for r in rows if r[4] and r[5] and r[4] > 0 and r[5] > 0 and r[1] and r[2] and r[3]]
    out = []
    for d, o, h, l, c, adj, v in rows:
        f = adj / c
        out.append((d, o * f, h * f, l * f, adj, v or 0, c))
    return out


def main():
    summ_f = DATA / "_summary.json"
    meta = json.loads(summ_f.read_text())["done"] if summ_f.exists() else {}
    codes = [k for k, v in meta.items() if v.get("type") == "Common Stock" and v.get("rows", 0) >= 800]
    ev = defaultdict(list)
    base_all, base_setup = [], []
    rng = np.random.default_rng(105)
    n = 0
    t0 = time.time()
    for k, code in enumerate(sorted(codes)):
        fp = DATA / f"{code.replace('/', '_')}.json.gz"
        if not fp.exists():
            continue
        rows = load(code.replace("/", "_"))
        if len(rows) < 800:
            continue
        n += 1
        d = [r[0] for r in rows]
        o = np.array([r[1] for r in rows]); h = np.array([r[2] for r in rows]); lo = np.array([r[3] for r in rows])
        c = np.array([r[4] for r in rows]); v = np.array([r[5] for r in rows], dtype=float); raw = np.array([r[6] for r in rows])
        val = raw * v
        s20, s50, s200 = R.sma(c, 20), R.sma(c, 50), R.sma(c, 200)
        minc500 = R.roll_min(c, 500)
        maxh750 = R.roll_max(h, 750)
        maxh250_prev = np.concatenate([[np.nan], R.roll_max(h, 250)[:-1]])
        v20, v250 = R.sma(val, 20), R.sma(val, 250)
        from numpy.lib.stride_tricks import sliding_window_view
        med20 = np.full(len(c), np.nan)
        med20[19:] = np.median(sliding_window_view(val, 20), axis=1)
        mturn = R.monthly_macd_turns(d, c)
        tradable = (raw >= 1.0) & (med20 >= 100_000)
        ep_start, ep_low, ep_pre, seen = None, None, None, set()
        for i in range(750, len(c) - 1):
            if d[i] < START or d[i] > END:
                continue
            if c[i] <= minc500[i] and c[i] <= 0.5 * maxh750[i]:
                lowday = i - int(np.argmin(lo[max(0, i - 59):i + 1][::-1]))
                ep_start, ep_low, seen = i, float(np.min(lo[max(0, i - 59):i + 1])), set()
                ep_pre = c[max(0, lowday - 20)]
            in_ep = ep_start is not None and i - ep_start <= 250
            if not in_ep:
                ep_start = None
            if tradable[i] and rng.random() < 0.05:
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
        if k % 2000 == 0:
            print(f"[105] {k}/{len(codes)} files {round((time.time() - t0) / 60)} min", flush=True)

    def summ(lst):
        if not lst:
            return {"n": 0}
        r = np.array([o["ret"] for _, o in lst])
        w = [o["worst"] for _, o in lst if o["x2"]]
        return {"n": len(lst), "x2_pct": round(100 * np.mean([o["x2"] for _, o in lst]), 1),
                "x5_pct": round(100 * np.mean([o["x5"] for _, o in lst]), 1), "x10_pct": round(100 * np.mean([o["x10"] for _, o in lst]), 2),
                "mean_ret_pct": round(100 * r.mean(), 1), "median_ret_pct": round(100 * np.median(r), 1),
                "hit_pct": round(100 * np.mean(r > 0), 1), "worst_before_2x_median_pct": round(100 * np.median(w), 1) if w else None}

    def tdiff(a, b, key="x2"):
        def cl(lst):
            by = defaultdict(list)
            for dd, o in lst:
                by[dd[:7]].append(float(o[key]))
            return np.array([np.mean(x) for x in by.values()])
        A, B = cl(a), cl(b)
        if len(A) < 3 or len(B) < 3:
            return None
        se = np.sqrt(A.var(ddof=1) / len(A) + B.var(ddof=1) / len(B))
        return round(float((A.mean() - B.mean()) / se), 2) if se > 0 else None

    periods = {"all": lambda dd: True, "2008_2018": lambda dd: dd < SPLIT, "2019_on": lambda dd: dd >= SPLIT}
    res = {"round": 105, "built": time.strftime("%Y-%m-%d"), "stocks": n, "codes_listed": len(codes),
           "baseline_all": {}, "baseline_setup": {}, "signals": {}}
    for pn, pf in periods.items():
        ba = [x for x in base_all if pf(x[0])]
        bs = [x for x in base_setup if pf(x[0])]
        res["baseline_all"][pn] = summ(ba)
        res["baseline_setup"][pn] = summ(bs)
        for s in SIGS + [f"L{k}" for k in range(1, 6)]:
            lst = [x for x in ev[s] if pf(x[0])]
            r = summ(lst)
            r["t_x2_vs_setup"] = tdiff(lst, bs, "x2")
            r["t_x5_vs_setup"] = tdiff(lst, bs, "x5")
            res["signals"].setdefault(s, {})[pn] = r
    verdict = {}
    for s in SIGS + [f"L{k}" for k in range(1, 6)]:
        a, b = res["signals"][s]["2008_2018"], res["signals"][s]["2019_on"]
        ta, tb = a.get("t_x2_vs_setup"), b.get("t_x2_vs_setup")
        if ta is None or tb is None:
            verdict[s] = "数据不够"
        elif a["n"] >= 100 and b["n"] >= 100 and ta >= 3 and tb >= 3:
            verdict[s] = "有效"
        elif ta >= 2 and tb >= 2:
            verdict[s] = "一般"
        else:
            verdict[s] = "无效"
    res["verdict"] = verdict
    res["main"] = {"signal": "R4", "verdict": verdict.get("R4"), "replicates_asx_round98": verdict.get("R4") == "有效"}
    out = os.environ.get("STUDY105_OUT", "out/study105_results.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(res, ensure_ascii=False))
    print("round 105 done (results only in the encrypted file)", flush=True)


if __name__ == "__main__":
    main()
