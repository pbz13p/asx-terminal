"""Round 28: trading halts — capital raisings and big announcements (very common on the ASX).

A halt shows up as 1-5 sessions with zero volume. When the stock reopens:
  - gap down >= 5% on >= 3x volume  -> usually a discounted placement / capital raising
  - gap up >= 5% on >= 3x volume    -> usually good news (takeover, contract, results, resource)
Entry at the reopening day's close (you can see the announcement and the first day), hold 5 / 20 / 60
sessions, net of spread + 0.1%, and vs the median tradable stock. Both periods; month-clustered t.
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
    stocks = {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) > 150:
            stocks[f.name[:-8]] = rows
    fwd = defaultdict(lambda: defaultdict(list))
    for rows in stocks.values():
        for i in range(21, len(rows) - 61, 5):
            if rows[i][6] >= .05 and rows[i][4] > 0:
                for h in HOLDS:
                    fwd[rows[i][0]][h].append(rows[i + h][4] / rows[i][4] - 1)
    med = {d: {h: sorted(v)[len(v) // 2] for h, v in hs.items() if len(v) > 30} for d, hs in fwd.items()}
    mk = sorted(med)
    agg = defaultdict(lambda: {h: {"net": [], "ex": [], "month": defaultdict(list)} for h in HOLDS})
    xdays = [r[0] for r in study.load("AXJO.INDX")]
    xidx = {d: k for k, d in enumerate(xdays)}
    for code, rows in stocks.items():
        n = len(rows)
        for j in range(25, n - 61):
            # halt = 1-5 ASX sessions missing from the file, or 1-5 zero-volume rows, right before j
            pre = j - 1
            while pre > 0 and rows[pre][5] == 0 and j - pre <= 6:
                pre -= 1
            zero_run = j - 1 - pre
            d0, d1 = rows[pre][0], rows[j][0]
            missed = (xidx[d1] - xidx[d0] - 1) if d0 in xidx and d1 in xidx else 0
            halted = missed + zero_run
            if not (1 <= halted <= 5) or rows[j][5] == 0 or pre < 22:
                continue
            d, o, h, l, c, v, raw = rows[j]
            pc = rows[pre][4]
            vols = sorted(r[5] for r in rows[pre - 20:pre + 1])
            turn = sorted(r[6] * r[5] for r in rows[pre - 20:pre + 1])[10]
            if pc <= 0 or o <= 0 or rows[pre][6] < .05 or vols[10] <= 0 or turn < 1e5:
                continue
            gap = o / pc - 1
            if v < 3 * vols[10] or abs(gap) < .05 or d < "2015-01-01":
                continue
            kind = "停牌后低开≥5%（多为配股/融资）" if gap < 0 else "停牌后高开≥5%（多为利好）"
            sp = [study2.cs_spread(rows[k - 1][2], rows[k - 1][3], rows[k][2], rows[k][3]) for k in range(pre - 19, pre + 1)]
            cost = study2.median([x for x in sp if x is not None]) + study.COMMISSION
            mj = bisect.bisect_right(mk, d) - 1
            if mj < 0:
                continue
            liq = "日成交≥50万" if turn >= 5e5 else "日成交10–50万"
            per = "2015–21" if d < study.SPLIT else "2022后"
            for hd in HOLDS:
                if hd not in med[mk[mj]]:
                    continue
                r = rows[j + hd][4] / c - 1
                for key in (kind, f"{kind}|{per}", f"{kind}|{liq}"):
                    a_ = agg[key][hd]
                    a_["net"].append(r - cost)
                    a_["ex"].append(r - med[mk[mj]][hd])
                    a_["month"][d[:7]].append(r - med[mk[mj]][hd])
    out = {}
    for k, hs in agg.items():
        out[k] = {}
        for h, a in hs.items():
            if not a["net"]:
                continue
            ms = [sum(v) / len(v) for v in a["month"].values()]
            mm = sum(ms) / len(ms)
            sd = math.sqrt(sum((x - mm) ** 2 for x in ms) / max(1, len(ms) - 1))
            out[k][str(h)] = {"n": len(a["net"]), "net": 100 * sum(a["net"]) / len(a["net"]),
                              "median_net": 100 * sorted(a["net"])[len(a["net"]) // 2],
                              "excess": 100 * sum(a["ex"]) / len(a["ex"]), "months": len(ms),
                              "t_month": mm / (sd / math.sqrt(len(ms))) if sd and len(ms) > 5 else None}
    p = Path(os.environ.get("STUDY28_OUT", study.DATA / "study28_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
