"""Round 33: reporting-season signals judged against the ASX 200 itself (round-24 lesson).

Same events as rounds 10-11 (reporting season, gap >= 5% on >= 3x volume), entry next open, but:
  - only stocks a private investor can trade size in (20-day median turnover >= $2M)
  - excess = stock return - ASX 200 accumulation index over the same window
  - t-stats clustered by season (Feb/Aug of each year)
Gap-up that held (close > open): hold 5 / 10 / 20 days. Gap-down: 5 / 20 / 60 days.
"""
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
import study2
from study10 import season


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    agg = defaultdict(lambda: defaultdict(lambda: {"ex": [], "net_ex": [], "season": defaultdict(list)}))
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 100:
            continue
        for i in range(21, len(rows) - 61):
            d, o, h, l, c, v, raw = rows[i]
            if d < "2015-01-01" or not season(d) or raw < .2:
                continue
            pc = rows[i - 1][4]
            vols = sorted(r[5] for r in rows[i - 20:i])
            turn = sorted(r[6] * r[5] for r in rows[i - 20:i])[10]
            if pc <= 0 or o <= 0 or vols[10] <= 0 or v < 3 * vols[10] or turn < 2e6:
                continue
            gap = o / pc - 1
            if gap >= .05 and c > o:
                kind, holds = "业绩跳涨站稳", (5, 10, 20)
            elif gap <= -.05:
                kind, holds = "业绩跳空大跌", (5, 20, 60)
            else:
                continue
            e = rows[i + 1][1]
            d0 = rows[i][0]
            if e <= 0 or d0 not in acc:
                continue
            sp = [study2.cs_spread(rows[k - 1][2], rows[k - 1][3], rows[k][2], rows[k][3]) for k in range(i - 19, i + 1)]
            cost = study2.median([x for x in sp if x is not None]) + study.COMMISSION
            sk = d[:4] + ("-02" if d[5:7] in ("02", "03") else "-08")
            for hd in holds:
                d1 = rows[i + hd][0]
                if d1 not in acc:
                    continue
                r = rows[i + hd][4] / e - 1
                m = acc[d1] / acc[d0] - 1
                for key in (kind, f"{kind}|{'2015–21' if d < study.SPLIT else '2022后'}"):
                    a = agg[key][hd]
                    a["ex"].append(r - m)
                    a["net_ex"].append(r - m - cost)
                    a["season"][sk].append(r - m)
    out = {}
    for k, hs in agg.items():
        out[k] = {}
        for h, a in hs.items():
            ss = [sum(v) / len(v) for v in a["season"].values()]
            mm = sum(ss) / len(ss)
            sd = math.sqrt(sum((x - mm) ** 2 for x in ss) / max(1, len(ss) - 1))
            out[k][str(h)] = {"n": len(a["ex"]), "ex": 100 * sum(a["ex"]) / len(a["ex"]),
                              "net_ex": 100 * sum(a["net_ex"]) / len(a["net_ex"]),
                              "median_ex": 100 * sorted(a["ex"])[len(a["ex"]) // 2], "seasons": len(ss),
                              "seasons_pos": 100 * sum(1 for x in ss if x > 0) / len(ss),
                              "t_season": mm / (sd / math.sqrt(len(ss))) if sd and len(ss) > 3 else None}
    p = Path(os.environ.get("STUDY33_OUT", study.DATA / "study33_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
