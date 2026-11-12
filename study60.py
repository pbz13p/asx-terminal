"""Round 60: blue-chip dips and pops (liquid stocks, 20-day median turnover >= $5M), outside reporting season
and without a trading halt. One-day move <= -5% / -8% or >= +5% / +8% (close to close). Buy at the next open,
hold 5 / 20 / 60 sessions, vs the ASX 200 accumulation index; month-clustered t; split 2015-21 / 2022-23 / 2024+."""
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
from study10 import season


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    xdays = [r[0] for r in study.load("AXJO.INDX")]
    xidx = {d: k for k, d in enumerate(xdays)}
    agg = defaultdict(lambda: defaultdict(lambda: {"x": [], "m": defaultdict(list), "p": defaultdict(list)}))
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        for i in range(21, len(rows) - 61):
            d, o, h, l, c, v, raw = rows[i]
            if d < "2015-01-01" or season(d) or d not in acc or raw < 1:
                continue
            pd_ = rows[i - 1][0]
            if pd_ in xidx and d in xidx and xidx[d] - xidx[pd_] != 1:
                continue
            turn = sorted(r[6] * r[5] for r in rows[i - 20:i])[10]
            if turn < 5e6:
                continue
            r1 = c / rows[i - 1][4] - 1
            kind = ("大跌≥8%" if r1 <= -.08 else "大跌5–8%" if r1 <= -.05 else
                    "大涨≥8%" if r1 >= .08 else "大涨5–8%" if r1 >= .05 else None)
            if not kind:
                continue
            e = rows[i + 1][1]
            if e <= 0:
                continue
            per = "2015–21" if d < "2022" else "2022–23" if d < "2024" else "2024后"
            for hd in (5, 20, 60):
                ed = rows[i + hd][0]
                if ed not in acc:
                    continue
                ex = rows[i + hd][4] / e - 1 - (acc[ed] / acc[d] - 1)
                a = agg[kind][hd]
                a["x"].append(ex)
                a["m"][d[:7]].append(ex)
                a["p"][per].append(ex)
    out = {}
    for k, hs in agg.items():
        out[k] = {}
        for h, a in hs.items():
            ms = [sum(v) / len(v) for v in a["m"].values()]
            mm = sum(ms) / len(ms)
            sd = math.sqrt(sum((x - mm) ** 2 for x in ms) / max(1, len(ms) - 1))
            out[k][str(h)] = {"n": len(a["x"]), "ex": round(100 * sum(a["x"]) / len(a["x"]), 2),
                              "median": round(100 * sorted(a["x"])[len(a["x"]) // 2], 2),
                              "t": round(mm / (sd / math.sqrt(len(ms))), 2) if sd and len(ms) > 5 else None,
                              "by_period": {p: round(100 * sum(v) / len(v), 2) for p, v in sorted(a["p"].items())}}
    p = Path(os.environ.get("STUDY60_OUT", study.DATA / "study60_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
