"""Round 56: small/mid-cap results-season gap-ups (>= 8%, held, 3x volume) as a real portfolio, 20-day holds.
(based on round 24 code)

Rules: each day, take that day's breakouts (round 21 definition; 20-day median turnover >= $2M so a
private investor can trade them). Buy at the next open with 10% of equity each, up to 10 positions,
biggest turnover first. Sell after 60 trading days, or earlier if the close falls 20% below entry
(variant without stop too). Costs: Corwin-Schultz spread + 0.1% each round trip. Idle cash earns the RBA
cash rate. Benchmark: ASX 200 accumulation index. Results 2015-2026 and per period.
"""
import bisect
import json
import math
import os
from collections import defaultdict, deque
from pathlib import Path

import study
import study2
import study7
from study10 import season

MAX_POS, SIZE, HOLD = 10, 0.10, 20
GAP = float(os.environ.get("S56_GAP", "0.08"))


def stats(eq, dates):
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    yrs = len(rets) / 252
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
    return {"cagr": 100 * ((eq[-1] / eq[0]) ** (1 / yrs) - 1), "maxdd": 100 * dd, "sharpe": m / sd * math.sqrt(252) if sd else 0,
            "from": dates[0], "to": dates[-1]}


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    days = [d for d in sorted(acc) if d >= "2015-01-01"]
    f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
    ck = sorted(f1)
    cash_rate = lambda d: f1[ck[bisect.bisect_right(ck, d) - 1]] / 100 / 252 if ck else 0
    px = {}
    events = defaultdict(list)
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 330:
            continue
        code = f.name[:-8]
        px[code] = {r[0]: (r[1], r[4]) for r in rows}
        for t in range(21, len(rows)):
            d, o, h, l, c, v, raw = rows[t]
            if d < "2014-12-01" or not season(d) or raw < .2:
                continue
            pc = rows[t - 1][4]
            vols = sorted(r[5] for r in rows[t - 20:t])
            turn = sorted(r[6] * r[5] for r in rows[t - 20:t])[10]
            if pc <= 0 or o <= 0 or vols[10] <= 0 or v < 3 * vols[10] or not (2e5 <= turn < 2e6):
                continue
            if o / pc - 1 < GAP or c <= o:
                continue
            sp = [study2.cs_spread(rows[k - 1][2], rows[k - 1][3], rows[k][2], rows[k][3]) for k in range(t - 19, t + 1)]
            cost = study2.median([x for x in sp if x is not None]) + study.COMMISSION
            events[d].append((turn, code, cost))
    out = {}
    for variant, stop in (("不止损", 0.0),):
        cash, pos, eq, trades = 1.0, [], [], []
        pending = []
        for i, d in enumerate(days):
            # buy pending signals at today's open
            for turn, code, cost in pending:
                if len(pos) >= MAX_POS or code in {p["code"] for p in pos}:
                    continue
                bar = px[code].get(d)
                if not bar or bar[0] <= 0:
                    continue
                total = cash + sum(p["units"] * px[p["code"]].get(p["last"], (0, p["entry"]))[1] for p in pos)
                amt = min(cash, SIZE * total)
                if amt < .02 * total:
                    continue
                cash -= amt
                pos.append({"code": code, "units": amt * (1 - cost / 2) / bar[0], "entry": bar[0], "days": 0, "last": d, "cost": cost})
            pending = []
            # mark to market, exits at close
            keep = []
            for p in pos:
                bar = px[p["code"]].get(d)
                if bar:
                    p["last"] = d
                p["days"] += 1
                c = px[p["code"]].get(p["last"])[1]
                if p["days"] >= HOLD or (stop and c < p["entry"] * stop):
                    proceeds = p["units"] * c * (1 - p["cost"] / 2)
                    cash += proceeds
                    trades.append(c / p["entry"] - 1 - p["cost"])
                else:
                    keep.append(p)
            pos = keep
            cash *= 1 + cash_rate(d)
            eq.append(cash + sum(p["units"] * px[p["code"]][p["last"]][1] for p in pos))
            pending = sorted(events.get(d, []), reverse=True)
        sp = next(k for k, d in enumerate(days) if d >= study.SPLIT)
        bench = [acc[d] for d in days]
        out[variant] = {"all": stats(eq, days), "2015–21": stats(eq[:sp + 1], days[:sp + 1]), "2022后": stats(eq[sp:], days[sp:]), "2024后": stats(eq[next(k for k, d in enumerate(days) if d >= "2024-01-01"):], days[next(k for k, d in enumerate(days) if d >= "2024-01-01"):]),
                        "trades": len(trades), "avg_trade": 100 * sum(trades) / len(trades),
                        "win": 100 * sum(1 for t in trades if t > 0) / len(trades),
                        "avg_positions": None}
    sp = next(k for k, d in enumerate(days) if d >= study.SPLIT)
    bench = [acc[d] for d in days]
    out["ASX 200 全收益（基准）"] = {"all": stats(bench, days), "2015–21": stats(bench[:sp + 1], days[:sp + 1]), "2022后": stats(bench[sp:], days[sp:])}
    p = Path(os.environ.get("STUDY56_OUT", study.DATA / "study56_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
