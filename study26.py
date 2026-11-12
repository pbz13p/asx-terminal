"""Round 26: true out-of-sample check of the 'hawkish RBA surprise' rule.

Set the threshold using only 2015-2021 RBA meetings (top third of 2-year-yield moves on decision day),
then apply it unchanged to 2022-2026 meetings: next-week ASX 200 return after 'hawkish' vs the rest,
and plan D (halve the ETF for 5 sessions) vs buy-and-hold over 2022-2026 only.
Also a placebo: the same rule on random non-RBA days.
"""
import bisect
import json
import math
import os
import random
from pathlib import Path

import study
import study4
import study7


def main():
    random.seed(26)
    f2 = study7.rba_table("f2")["FCMYGBAG2D"]
    yk = sorted(f2)
    f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
    ck = sorted(f1)
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    px = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    days = [d for d in sorted(acc) if d in px]
    idx = {d: i for i, d in enumerate(days)}
    rba = sorted(d for d in study4.fallback_events()["澳联储 利率决议"] if d in f2 and d in idx)

    def dy(d):
        k = yk.index(d)
        return f2[d] - f2[yk[k - 1]] if k else None

    def week(d):
        i = idx[d]
        return px[days[i + 5]] / px[d] - 1 if i + 5 < len(days) else None
    ins = [(d, dy(d)) for d in rba if "2015" <= d < study.SPLIT and dy(d) is not None]
    cut = sorted(x[1] for x in ins)[int(len(ins) * 2 / 3)]
    oos = [(d, dy(d), week(d)) for d in rba if d >= study.SPLIT and dy(d) is not None and week(d) is not None]
    hawk = [x for x in oos if x[1] >= cut]
    rest = [x for x in oos if x[1] < cut]
    mean = lambda a: 100 * sum(a) / len(a) if a else None

    def plan(threshold, start, dates_set):
        r_bh, r_d, cut_left = [], [], 0
        for i in range(1, len(days)):
            d, p = days[i], days[i - 1]
            if p in dates_set and p in f2 and dy(p) is not None and dy(p) >= threshold:
                cut_left = 5
            if d >= start:
                r = acc[d] / acc[p] - 1
                c = f1[ck[bisect.bisect_right(ck, p) - 1]] / 100 / 252
                w = .5 if cut_left > 0 else 1.0
                r_bh.append(r)
                r_d.append(w * r + (1 - w) * c - (0.0005 if cut_left in (5, 1) else 0))
            cut_left = max(0, cut_left - 1)
        cg = lambda rs: 100 * (math.prod(1 + x for x in rs) ** (252 / len(rs)) - 1)
        return cg(r_bh), cg(r_d)
    bh, d_plan = plan(cut, study.SPLIT, set(rba))
    # placebo: random sets of non-RBA days with the same count, same threshold
    others = [d for d in days if d >= "2015-01-01" and d not in rba and d in f2]
    gains = []
    for _ in range(200):
        fake = set(random.sample(others, len(rba)))
        b2, d2 = plan(cut, study.SPLIT, fake)
        gains.append(d2 - b2)
    out = {"threshold_bp": round(100 * cut, 2), "in_sample_meetings": len(ins),
           "oos_meetings": len(oos), "oos_hawk": {"n": len(hawk), "week": mean([x[2] for x in hawk]),
                                                 "dates": [(x[0], round(100 * x[1], 1), round(100 * x[2], 2)) for x in hawk]},
           "oos_rest": {"n": len(rest), "week": mean([x[2] for x in rest])},
           "oos_plan": {"buy_hold_cagr": bh, "plan_d_cagr": d_plan, "gain": d_plan - bh},
           "placebo_gain_mean": sum(gains) / len(gains), "placebo_share_beating": 100 * sum(1 for g in gains if g >= d_plan - bh) / len(gains)}
    p = Path(os.environ.get("STUDY26_OUT", study.DATA / "study26_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
