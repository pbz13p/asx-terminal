"""Round 50: results-season gap-UPs (held: close > open) by size, vs the ASX 200 — grid like round 44.
Grid: gap threshold (3/5/8/12%), volume multiple (2/3/5x), liquidity tier; 20-day return from next open vs
ASX 200 accumulation; month-clustered t; both periods."""
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
from study10 import season


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    agg = defaultdict(lambda: defaultdict(list))
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        for i in range(21, len(rows) - 21):
            d, o, h, l, c, v, raw = rows[i]
            if d < "2015-01-01" or not season(d) or raw < .2 or d not in acc:
                continue
            pc = rows[i - 1][4]
            vols = sorted(r[5] for r in rows[i - 20:i])
            turn = sorted(r[6] * r[5] for r in rows[i - 20:i])[10]
            if pc <= 0 or o <= 0 or vols[10] <= 0 or turn < 2e5:
                continue
            gap = o / pc - 1
            if gap < .03 or c <= o:
                continue
            e, d1 = rows[i + 1][1], rows[i + 20][0]
            if e <= 0 or d1 not in acc:
                continue
            ex = rows[i + 20][4] / e - 1 - (acc[d1] / acc[d] - 1)
            tier = "大" if turn >= 2e6 else "中小"
            per = "2015–21" if d < study.SPLIT else "2022后"
            vx = v / vols[10]
            for g in (.03, .05, .08, .12):
                for m in (2, 3, 5):
                    if gap >= g and vx >= m:
                        agg[f"涨{int(g*100)}%|{m}倍量|{tier}"][per].append((d[:7], ex))
    out = {}
    for k, ps in agg.items():
        row = {}
        for per, xs in ps.items():
            by = defaultdict(list)
            for mth, x in xs:
                by[mth].append(x)
            ms = [sum(v) / len(v) for v in by.values()]
            mm = sum(ms) / len(ms)
            sd = math.sqrt(sum((x - mm) ** 2 for x in ms) / max(1, len(ms) - 1))
            row[per] = {"n": len(xs), "ex": round(100 * sum(x for _, x in xs) / len(xs), 2),
                        "t": round(mm / (sd / math.sqrt(len(ms))), 2) if sd and len(ms) > 3 else None}
        out[k] = row
    neg = sum(1 for v in out.values() if all(p in v and v[p]["ex"] > 0 for p in ("2015–21", "2022后")))
    res = {"combos": len(out), "positive_both_periods": neg, "grid": out}
    p = Path(os.environ.get("STUDY50_OUT", study.DATA / "study50_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "grid"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
