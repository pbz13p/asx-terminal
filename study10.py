"""Round 10: reporting-season drift on the ASX (post-earnings-announcement drift proxy).

ASX companies report mostly in Feb (half-year / full-year Dec balancers) and Aug (June year-ends).
Event: during reporting season (Feb 5 - Mar 5, Aug 5 - Sep 5), a stock gaps >= 5% (up or down) at the open
on >= 3x its 20-day median volume — almost always a results reaction. Entry: next day's open
(the day after the reaction), so the result is known. Hold 5 / 20 / 60 trading days.
Compared with the same signal outside reporting season, and with the ASX 200 over the same window.
Costs: Corwin-Schultz spread (20-day median) + 0.1% commission. Tradable universe only.
"""
import json
import os
from collections import defaultdict
from pathlib import Path

import study
import study2

HOLDS = (5, 20, 60)


def season(d):
    md = d[5:]
    return ("02-05" <= md <= "03-05") or ("08-05" <= md <= "09-05")


def main():
    xj = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    agg = defaultdict(lambda: defaultdict(lambda: {"n": 0, "gross": 0.0, "net": 0.0, "ex": 0.0, "win": 0, "dates": set()}))
    by_year = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    files = sorted(study.CACHE.glob("*.AU.json"))
    for f in files:
        rows = study.load(f.name[:-5])
        if len(rows) < 100:
            continue
        sp = [None] * len(rows)
        for i in range(1, len(rows)):
            sp[i] = study2.cs_spread(rows[i - 1][2], rows[i - 1][3], rows[i][2], rows[i][3])
        for i in range(21, len(rows) - 61):
            d, o, h, l, c, v, raw = rows[i]
            pc = rows[i - 1][4]
            if raw < .2 or pc <= 0 or o <= 0:
                continue
            vols = sorted(rows[k][5] for k in range(i - 20, i))
            turn = sorted(rows[k][6] * rows[k][5] for k in range(i - 20, i))[10]
            if turn < 5e5 or vols[10] <= 0 or v < 3 * vols[10]:
                continue
            gap = o / pc - 1
            if abs(gap) < .05:
                continue
            day_r = c / o - 1  # reaction after the open on the event day
            side = "高开≥5%" if gap > 0 else "低开≥5%"
            follow = "当天继续" if (day_r > 0) == (gap > 0) else "当天回吐"
            spreads = [x for x in sp[i - 19:i + 1] if x is not None]
            cost = study2.median(spreads) + study.COMMISSION
            entry = rows[i + 1][1]
            if entry <= 0 or rows[i + 1][0] not in xj:
                continue
            seas = "业绩季" if season(d) else "非业绩季"
            for hd in HOLDS:
                ex_d = rows[i + 1 + hd - 1][0]
                if ex_d not in xj:
                    continue
                r = rows[i + 1 + hd - 1][4] / entry - 1
                m = xj[ex_d] / xj[rows[i][0]] - 1 if rows[i][0] in xj else 0
                for key in (f"{seas}|{side}", f"{seas}|{side}|{follow}"):
                    for per in ("all", "in" if d < study.SPLIT else "out"):
                        a = agg[f"{key}|{per}"][hd]
                        a["n"] += 1
                        a["gross"] += r
                        signed = r if gap > 0 else -r  # short side for gap-downs
                        a["net"] += signed - cost
                        a["ex"] += r - m
                        a["win"] += signed > cost
                        a["dates"].add(d)
                if hd == 20 and seas == "业绩季":
                    b = by_year[d[:4]][side]
                    b[0] += 1
                    b[1] += r - m
    out = {}
    for k, hs in agg.items():
        out[k] = {str(h): {"n": a["n"], "dates": len(a["dates"]), "gross": 100 * a["gross"] / a["n"],
                           "excess": 100 * a["ex"] / a["n"], "net_dir": 100 * a["net"] / a["n"], "win": 100 * a["win"] / a["n"]}
                  for h, a in hs.items() if a["n"]}
    years = {y: {s: {"n": v[0], "excess20": 100 * v[1] / v[0]} for s, v in ss.items()} for y, ss in sorted(by_year.items())}
    p = Path(os.environ.get("STUDY10_OUT", study.DATA / "study10_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"groups": out, "years": years}, ensure_ascii=False), encoding="utf-8")
    print("round 10 done")


if __name__ == "__main__":
    main()
