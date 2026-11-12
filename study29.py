"""Round 29: factor portfolios among liquid ASX stocks, judged against the ASX 200 (the round-24 lesson).

Universe each month-end: stocks with 20-day median turnover >= $5M and price >= $1 (roughly the ASX 200).
Sort into 5 equal-weight portfolios by (a) 12-1 month momentum, (b) 60-day volatility, (c) distance to the
52-week high. Hold one month (rebalanced monthly; costs: 0.25% per unit of one-way turnover).
Returns use dividend-adjusted prices; benchmark = ASX 200 accumulation index. 2015-2026 and per period.
"""
import bisect
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study

COST = 0.0025


def stats(rets):
    if not rets:
        return None
    eq = peak = 1.0
    dd = 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
    return {"cagr": 100 * (eq ** (12 / len(rets)) - 1), "maxdd": 100 * dd, "sharpe": m / sd * math.sqrt(12) if sd else 0}


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    adays = sorted(acc)
    month_ends = []
    for a, b in zip(adays, adays[1:]):
        if a[:7] != b[:7] and a >= "2014-12-01":
            month_ends.append(a)
    stocks = {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) > 300:
            stocks[f.name[:-8]] = ({r[0]: k for k, r in enumerate(rows)}, rows)
    factors = ("动量(12-1个月)", "低波动(60天)", "离52周高点近")
    port = {f: {q: [] for q in range(5)} for f in factors}
    prev_hold = {f: {q: set() for q in range(5)} for f in factors}
    bench, dates = [], []
    for mi in range(len(month_ends) - 1):
        d, nxt = month_ends[mi], month_ends[mi + 1]
        cand = []
        for code, (idx, rows) in stocks.items():
            if d not in idx or nxt not in idx:
                continue
            i, j = idx[d], idx[nxt]
            if i < 260 or rows[i][6] < 1:
                continue
            turn = sorted(r[6] * r[5] for r in rows[i - 19:i + 1])[10]
            if turn < 5e6:
                continue
            c = [r[4] for r in rows[i - 252:i + 1]]
            mom = c[-22] / c[0] - 1
            rets = [math.log(c[k] / c[k - 1]) for k in range(len(c) - 60, len(c))]
            vol = math.sqrt(sum(x * x for x in rets) / 60)
            near = c[-1] / max(c) - 1
            fwd = rows[j][4] / rows[i][4] - 1
            cand.append((code, mom, -vol, near, fwd))
        if len(cand) < 50:
            continue
        dates.append(nxt)
        bench.append(acc[nxt] / acc[d] - 1)
        for fi, f in enumerate(factors):
            srt = sorted(cand, key=lambda x: x[1 + fi])
            n = len(srt)
            for q in range(5):
                grp = srt[q * n // 5:(q + 1) * n // 5]
                codes = {x[0] for x in grp}
                turnover = 1 - len(codes & prev_hold[f][q]) / max(1, len(codes))
                r = sum(x[4] for x in grp) / len(grp) - COST * turnover
                port[f][q].append(r)
                prev_hold[f][q] = codes
    sp = next(k for k, d in enumerate(dates) if d >= study.SPLIT)
    out = {"months": len(dates), "benchmark": {"all": stats(bench), "2015–21": stats(bench[:sp]), "2022后": stats(bench[sp:])}}
    for f in factors:
        out[f] = {f"Q{q + 1}{'(最低)' if q == 0 else '(最高)' if q == 4 else ''}":
                  {"all": stats(v), "2015–21": stats(v[:sp]), "2022后": stats(v[sp:])} for q, v in port[f].items()}
    p = Path(os.environ.get("STUDY29_OUT", study.DATA / "study29_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
