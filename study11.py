"""Round 11: is the reporting-season signal real money?

Buy the next open after a results gap-up (>= 5% gap on >= 3x volume, closed above its open) in reporting
season, hold 5 trading days, net of spread + commission. Report: date-clustered t-stat, each season's
result (Feb / Aug of each year), share of seasons that made money, typical daily turnover of the stocks
(capacity), and a stricter variant (gap >= 8%). Also the gap-down side over 20 days.
"""
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
import study2
from study10 import season


def tstat(by_date):
    xs = [sum(v) / len(v) for v in by_date.values()]
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd else None


def main():
    variants = {"高开≥5%站稳·持5天": (0.05, 5, 1), "高开≥8%站稳·持5天": (0.08, 5, 1),
                "高开≥5%站稳·持10天": (0.05, 10, 1), "低开≥5%·20天后股价": (0.05, 20, -1)}
    res = {k: {"by_date": defaultdict(list), "seasons": defaultdict(list), "turn": [], "all": []} for k in variants}
    for f in sorted(study.CACHE.glob("*.AU.json")):
        rows = study.load(f.name[:-5])
        if len(rows) < 100:
            continue
        for i in range(21, len(rows) - 21):
            d, o, h, l, c, v, raw = rows[i]
            if not season(d):
                continue
            pc = rows[i - 1][4]
            if raw < .2 or pc <= 0 or o <= 0:
                continue
            vols = sorted(rows[k][5] for k in range(i - 20, i))
            turns = sorted(rows[k][6] * rows[k][5] for k in range(i - 20, i))
            if turns[10] < 5e5 or vols[10] <= 0 or v < 3 * vols[10]:
                continue
            gap = o / pc - 1
            sp = [study2.cs_spread(rows[k - 1][2], rows[k - 1][3], rows[k][2], rows[k][3]) for k in range(i - 19, i + 1)]
            cost = study2.median([x for x in sp if x is not None]) + study.COMMISSION
            entry = rows[i + 1][1]
            if entry <= 0:
                continue
            for name, (g, hold, side) in variants.items():
                if side == 1 and not (gap >= g and c > o):
                    continue
                if side == -1 and not gap <= -g:
                    continue
                r = rows[i + hold][4] / entry - 1
                val = r - cost if side == 1 else r
                x = res[name]
                x["by_date"][d].append(val)
                x["seasons"][d[:4] + ("-02" if d[5:7] in ("02", "03") else "-08")].append(val)
                x["turn"].append(turns[10])
                x["all"].append(val)
    out = {}
    for name, x in res.items():
        a = x["all"]
        seas = {s: {"n": len(v), "mean": 100 * sum(v) / len(v)} for s, v in sorted(x["seasons"].items())}
        pos = sum(1 for v in seas.values() if v["mean"] > 0)
        tu = sorted(x["turn"])
        out[name] = {"n": len(a), "mean": 100 * sum(a) / len(a) if a else None, "t": tstat(x["by_date"]),
                     "median": 100 * sorted(a)[len(a) // 2] if a else None, "seasons": seas,
                     "seasons_positive": pos, "seasons_total": len(seas),
                     "turnover_median": tu[len(tu) // 2] if tu else None, "turnover_p25": tu[len(tu) // 4] if tu else None}
    p = Path(os.environ.get("STUDY11_OUT", study.DATA / "study11_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("round 11 done")


if __name__ == "__main__":
    main()
