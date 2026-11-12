"""Round 21: 52-week-high breakouts — the stock-level trend signal that held up in round 12.

Event: close at a new 52-week high, after having been at least 15% below that high within the last
60 trading days (a real base, not a stock grinding up every day), with volume >= 2x its 20-day median.
Entry next open; hold 5 / 20 / 60 days; net of spread + 0.1%; compared with the median tradable stock
over the same window (computed from a weekly cross-section). Split by liquidity, by whether the ASX 200
is above its 200-day average, and by period.
"""
import bisect
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
import study2

HOLDS = (5, 20, 60)


def main():
    xj = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    xd = sorted(xj)
    stocks = {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) > 330:
            stocks[f.name[:-8]] = rows
    # market median forward return by date (all tradable stocks), for the excess benchmark
    fwd = defaultdict(lambda: defaultdict(list))
    for rows in stocks.values():
        for i in range(21, len(rows) - 61, 5):
            if rows[i][6] < .2 or rows[i + 1][1] <= 0:
                continue
            for h in HOLDS:
                fwd[rows[i][0]][h].append(rows[i + h][4] / rows[i + 1][1] - 1)
    med = {d: {h: sorted(v)[len(v) // 2] for h, v in hs.items() if len(v) > 30} for d, hs in fwd.items()}
    mkeys = sorted(med)
    agg = defaultdict(lambda: {h: [] for h in HOLDS})
    cohort = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # key -> hold -> month -> excess
    for code, rows in stocks.items():
        from collections import deque
        cl = [r[4] for r in rows]
        n = len(cl)
        prev_hi = [None] * n
        low60 = [None] * n
        dq, dq2 = deque(), deque()
        for t in range(n):
            # window [t-252, t-1] for max; [t-60, t-1] for min
            if t >= 1:
                x = t - 1
                while dq and cl[dq[-1]] <= cl[x]:
                    dq.pop()
                dq.append(x)
                while dq2 and cl[dq2[-1]] >= cl[x]:
                    dq2.pop()
                dq2.append(x)
            while dq and dq[0] < t - 252:
                dq.popleft()
            while dq2 and dq2[0] < t - 60:
                dq2.popleft()
            if t >= 252:
                prev_hi[t] = cl[dq[0]]
                low60[t] = cl[dq2[0]]
        for i in range(252, len(rows) - 61):
            d, o, h, l, c, v, raw = rows[i]
            if d < "2015-01-01" or raw < .2:
                continue
            if c <= prev_hi[i] or low60[i] > prev_hi[i] * .85:
                continue
            vols = sorted(r[5] for r in rows[i - 20:i])
            turn = sorted(r[6] * r[5] for r in rows[i - 20:i])[10]
            if vols[10] <= 0 or v < 2 * vols[10] or turn < 5e5:
                continue
            e = rows[i + 1][1]
            if e <= 0:
                continue
            sp = [study2.cs_spread(rows[k - 1][2], rows[k - 1][3], rows[k][2], rows[k][3]) for k in range(i - 19, i + 1)]
            cost = study2.median([x for x in sp if x is not None]) + study.COMMISSION
            j = bisect.bisect_right(mkeys, d) - 1
            if j < 0:
                continue
            m = med[mkeys[j]]
            k = bisect.bisect_right(xd, d) - 1
            trend = None
            if k >= 200:
                ma = sum(xj[xd[t]] for t in range(k - 199, k + 1)) / 200
                trend = "大盘在200日线上" if xj[xd[k]] > ma else "大盘在200日线下"
            liq = "日成交>500万" if turn >= 5e6 else "日成交50–500万"
            per = "2015–21" if d < study.SPLIT else "2022后"
            for hd in HOLDS:
                if hd not in m:
                    continue
                r = rows[i + hd][4] / e - 1
                for key in ("全部", per, liq, trend):
                    if key:
                        agg[key][hd].append((r - cost, r - m[hd]))
                        cohort[key][hd][d[:7]].append(r - m[hd])
    out = {}
    for k, hs in agg.items():
        out[k] = {str(h): {"n": len(v), "net": 100 * sum(x[0] for x in v) / len(v), "excess": 100 * sum(x[1] for x in v) / len(v),
                           "median_net": 100 * sorted(x[0] for x in v)[len(v) // 2], "win": 100 * sum(1 for x in v if x[0] > 0) / len(v)}
                  for h, v in hs.items() if v}
        for h in HOLDS:
            ms = [sum(v) / len(v) for v in cohort[k][h].values()]
            if len(ms) > 5 and str(h) in out[k]:
                mm = sum(ms) / len(ms)
                sd = math.sqrt(sum((x - mm) ** 2 for x in ms) / (len(ms) - 1))
                out[k][str(h)]["months"] = len(ms)
                out[k][str(h)]["month_pos"] = 100 * sum(1 for x in ms if x > 0) / len(ms)
                out[k][str(h)]["t_month"] = mm / (sd / math.sqrt(len(ms))) if sd else None
    p = Path(os.environ.get("STUDY21_OUT", study.DATA / "study21_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
