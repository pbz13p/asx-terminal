"""Round 91: situation base rates -- "a stock like CND today: how often does it fall further, go sideways, or rise?"
(Frank 2026-11-20, CND at 1.5c after -11.8% on the day.)

Every ASX stock 2015 on, delisted included, round-80 consolidation fix. A day qualifies if:
  raw close 1.0-2.5c, the day's close is down >= 10% on the previous close, 20-day median $ traded $20k-$300k.
Variants: + 60-day return <= -30% (already sliding); + 1-year drawdown from the high >= 70%.
Outcome from the NEXT open (what you could actually do), on adjusted prices:
  5 / 20 / 60 trading days: down (< -10%), sideways (-10%..+10%), up (> +10%) -- at 1.5c one tick is ~7%, so
  +-10% is about one tick either way; plus median return, chance of doubling and of halving within 250 days,
  and chance of being delisted / no trading at the end (counted as -100% only if the data simply stops, flagged).
Also the same numbers for CND's own history (all qualifying days), for comparison.
"""
import json
import os
from pathlib import Path

import numpy as np

import study
import study80

OUT = Path(os.environ.get("STUDY91_OUT", "out/study91_results.json"))


def main():
    cal = [r[0] for r in study.load("AXJO.INDX")]
    study80.study_last = cal[-1]
    res = {"method": __doc__.strip(), "groups": {}}
    buckets = {k: [] for k in ("基础", "基础+60天跌30%以上", "基础+离一年高点跌70%以上", "CND自己")}
    for f in sorted(study.CACHE.glob("*.AU.json")):
        code = f.name[:-8]
        rows = study.load(f.name[:-5])
        if len(rows) < 300:
            continue
        rows = [r + (r[6] * r[5],) for r in rows]
        rows, _ = study80.fix_consolidations(rows)
        rows = study80.to_calendar(rows, cal)
        d = [r[0] for r in rows]
        o = np.array([r[1] for r in rows], float)
        c = np.array([r[4] for r in rows], float)
        raw = np.array([r[6] for r in rows], float)
        val = np.array([r[7] for r in rows], float)
        n = len(rows)
        for i in range(260, n - 1):
            if d[i] < "2015-01-01" or not (0.010 <= raw[i] <= 0.025) or raw[i - 1] <= 0:
                continue
            if raw[i] / raw[i - 1] - 1 > -0.10:
                continue
            med = float(np.median(val[i - 19:i + 1]))
            if not (2e4 <= med <= 3e5):
                continue
            e = i + 1
            if o[e] <= 0:
                continue
            out = {"date": d[i]}
            for h in (5, 20, 60):
                k = min(e + h - 1, n - 1)
                out[h] = c[k] / o[e] - 1
                out[f"cut{h}"] = e + h - 1 > n - 1
            fut = c[e:min(n, e + 250)]
            out["x2"] = bool(fut.max() >= 2 * o[e]) if len(fut) else False
            out["half"] = bool(fut.min() <= 0.5 * o[e]) if len(fut) else False
            out["ended"] = n - 1 < e + 249 and d[-1] < cal[-1]
            r60 = c[i] / c[i - 60] - 1 if c[i - 60] > 0 else 0
            dd = c[i] / c[i - 250:i + 1].max() - 1
            if code == "CND":
                buckets["CND自己"].append(out)
                continue
            buckets["基础"].append(out)
            if r60 <= -0.30:
                buckets["基础+60天跌30%以上"].append(out)
            if dd <= -0.70:
                buckets["基础+离一年高点跌70%以上"].append(out)
    for name, L in buckets.items():
        g = {"n": len(L), "股票日数说明": "同一只股票可能出现多次"}
        for h in (5, 20, 60):
            x = np.array([r[h] for r in L if not r[f"cut{h}"]])
            if len(x) == 0:
                continue
            g[f"{h}天"] = {"n": int(len(x)), "跌>10%": round(100 * float((x < -0.10).mean()), 1),
                          "平(±10%)": round(100 * float(((x >= -0.10) & (x <= 0.10)).mean()), 1),
                          "涨>10%": round(100 * float((x > 0.10).mean()), 1),
                          "中位数%": round(100 * float(np.median(x)), 1), "平均%": round(100 * float(np.mean(np.clip(x, -1, 5))), 1)}
        if L:
            g["一年内翻倍%"] = round(100 * float(np.mean([r["x2"] for r in L])), 1)
            g["一年内腰斩%"] = round(100 * float(np.mean([r["half"] for r in L])), 1)
            g["之后退市或停牌%"] = round(100 * float(np.mean([r["ended"] for r in L])), 1)
        res["groups"][name] = g
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("[91] done", flush=True)


if __name__ == "__main__":
    main()
