"""Round 43: is the 'fear boost' knife-edge? Plan: 60% base, 100% for N sessions after fear
(VIX >= X or ASX 200 down > D over 3 months), nothing else. Grid over X, D, N; gain vs buy-and-hold, both periods."""
import bisect
import json
import math
import os
from pathlib import Path

import study
import study7


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    px = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    vix = {r[0]: r[4] for r in study.load("VIX.INDX")}
    vk = sorted(vix)
    f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
    ck = sorted(f1)
    days = [d for d in sorted(acc) if d in px and d >= "2014-06-01"]
    cg = lambda rs: 100 * (math.prod(1 + x for x in rs) ** (252 / len(rs)) - 1)
    out = {}
    for X in (20, 25, 30):
        for D in (.05, .08, .10):
            for N in (20, 40, 60, 90):
                for base in (.6, .8):
                    left, prev, pl, bh, dates = 0, None, [], [], []
                    for i in range(70, len(days)):
                        d, p = days[i], days[i - 1]
                        j = bisect.bisect_left(vk, p) - 1
                        v = vix[vk[j]] if j >= 0 else 0
                        if v >= X or px[p] / px[days[i - 64]] - 1 < -D:
                            left = N
                        w = 1.0 if left > 0 else base
                        if d >= "2015-01-01":
                            r = acc[d] / acc[p] - 1
                            c = f1[ck[bisect.bisect_right(ck, p) - 1]] / 100 / 252
                            pl.append(w * r + (1 - w) * c - (.001 * abs(w - prev) if prev is not None else 0))
                            bh.append(r)
                            dates.append(d)
                            prev = w
                        left = max(0, left - 1)
                    sp = next(k for k, dd in enumerate(dates) if dd >= study.SPLIT)
                    out[f"VIX≥{X}|跌{int(D*100)}%|{N}天|平时{int(base*100)}%"] = {
                        "全期": round(cg(pl) - cg(bh), 2), "2015–21": round(cg(pl[:sp]) - cg(bh[:sp]), 2), "2022后": round(cg(pl[sp:]) - cg(bh[sp:]), 2)}
    vals = [v["全期"] for v in out.values()]
    summary = {"combos": len(vals), "positive": sum(1 for x in vals if x > 0), "median_gain": sorted(vals)[len(vals) // 2],
               "both_periods_positive": sum(1 for v in out.values() if v["2015–21"] > 0 and v["2022后"] > 0)}
    p = Path(os.environ.get("STUDY43_OUT", study.DATA / "study43_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"summary": summary, "grid": out}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
