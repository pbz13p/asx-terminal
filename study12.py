"""Round 12: does the direction panel's stock score mean anything?

Rebuild the panel's stock-level signals for every tradable stock on every Friday since 2015:
near 52-week high (+1) / far from it (-1), 5-day run >= 15% (-1) / drop >= 15% (+1), trend up (+1) /
down (-1), big up day outside reporting season (-1) / inside (+1), big down day inside season (-1) /
outside (+1). Then forward 5 / 20-day return from next open, minus the same-day median of all tradable
stocks (so dividends and market moves cancel). Grouped by score; both periods.
"""
import bisect
import json
import os
from collections import defaultdict
from pathlib import Path

import study
import study2
from study10 import season


def score(rows, i):
    c = [r[4] for r in rows[max(0, i - 251):i + 1]]
    last = c[-1]
    s, parts = 0, []
    hi = max(c)
    if last / hi - 1 >= -.05:
        s += 1; parts.append("near_hi")
    elif last / hi - 1 <= -.40:
        s -= 1; parts.append("far_hi")
    r5 = last / rows[i - 5][4] - 1
    if r5 >= .15:
        s -= 1; parts.append("run5")
    elif r5 <= -.15:
        s += 1; parts.append("drop5")
    if len(c) >= 200:
        m50, m200 = sum(c[-50:]) / 50, sum(c[-200:]) / 200
        if last > m50 > m200:
            s += 1; parts.append("trend_up")
        elif last < m50 < m200:
            s -= 1; parts.append("trend_dn")
    d1 = last / rows[i - 1][4] - 1
    if d1 >= .10:
        s += 1 if season(rows[i][0]) else -1; parts.append("bigup_s" if season(rows[i][0]) else "bigup")
    elif d1 <= -.05:
        s += -1 if season(rows[i][0]) else 1; parts.append("bigdn_s" if season(rows[i][0]) else "bigdn")
    return s, parts


def main():
    per_date = defaultdict(list)  # date -> [(score, parts, r5, r20, cost)]
    for f in sorted(study.CACHE.glob("*.AU.json")):
        rows = study.load(f.name[:-5])
        if len(rows) < 300:
            continue
        for i in range(252, len(rows) - 21):
            d = rows[i][0]
            if d < "2015-01-01":
                continue
            # every Friday (weekday 4)
            from datetime import date
            if date.fromisoformat(d).weekday() != 4:
                continue
            raw = rows[i][6]
            turn = sorted(rows[k][6] * rows[k][5] for k in range(i - 20, i))[10]
            if raw < .2 or turn < 5e5 or rows[i + 1][1] <= 0:
                continue
            sc, parts = score(rows, i)
            e = rows[i + 1][1]
            sp = [study2.cs_spread(rows[k - 1][2], rows[k - 1][3], rows[k][2], rows[k][3]) for k in range(i - 9, i + 1)]
            cost = study2.median([x for x in sp if x is not None]) + study.COMMISSION
            per_date[d].append((sc, parts, rows[i + 5][4] / e - 1, rows[i + 20][4] / e - 1, cost))
    buckets = defaultdict(lambda: {"r5": [], "r20": [], "per": {"in": [], "out": []}, "cost": []})
    parts_b = defaultdict(lambda: {"r20": [], "in": [], "out": []})
    for d, xs in per_date.items():
        if len(xs) < 30:
            continue
        m5 = sorted(x[2] for x in xs)[len(xs) // 2]
        m20 = sorted(x[3] for x in xs)[len(xs) // 2]
        per = "in" if d < study.SPLIT else "out"
        for sc, parts, r5, r20, cost in xs:
            k = "≤-2" if sc <= -2 else "≥+2" if sc >= 2 else f"{sc:+d}"
            b = buckets[k]
            b["r5"].append(r5 - m5)
            b["r20"].append(r20 - m20)
            b["per"][per].append(r20 - m20)
            b["cost"].append(cost)
            for p in parts:
                parts_b[p]["r20"].append(r20 - m20)
                parts_b[p][per].append(r20 - m20)
    mean = lambda a: 100 * sum(a) / len(a) if a else None
    out = {"buckets": {k: {"n": len(v["r5"]), "ex5": mean(v["r5"]), "ex20": mean(v["r20"]), "in20": mean(v["per"]["in"]),
                           "out20": mean(v["per"]["out"]), "cost": mean(v["cost"])} for k, v in sorted(buckets.items())},
           "parts": {k: {"n": len(v["r20"]), "ex20": mean(v["r20"]), "in20": mean(v["in"]), "out20": mean(v["out"])}
                     for k, v in parts_b.items()}}
    p = Path(os.environ.get("STUDY12_OUT", study.DATA / "study12_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
