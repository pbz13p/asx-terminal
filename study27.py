"""Round 27: macro-state sector tilt, tested out of sample.

States (weekly, known at the time): rate expectations from the AU 2y yield 3-month change
(cut < -0.5%, hike > +0.5%, else flat) and VIX (>=25 panic, <15 calm, else normal).
Step 1 (2015-2021 only): for each state, pick the 2 sectors with the best next-20-day excess return
vs the ASX 200. Step 2 (2022-2026): every week, hold 50% ASX 200 + 25% in each picked sector for the
current state; measure the excess return of that portfolio vs the ASX 200, with 0.1% cost on changes.
Price indices on both sides, so dividends roughly cancel.
"""
import bisect
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
import study7
from study9 import SECTORS


def main():
    study.download(["AXJO.INDX", "VIX.INDX"] + list(SECTORS.values()))
    xj = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    sec = {k: {r[0]: r[4] for r in study.load(v)} for k, v in SECTORS.items()}
    sec = {k: v for k, v in sec.items() if len(v) > 1500 and k != "小盘"}
    vix = {r[0]: r[4] for r in study.load("VIX.INDX")}
    vk = sorted(vix)
    au2 = study7.rba_table("f2")["FCMYGBAG2D"]
    ak = sorted(au2)
    days = [d for d in sorted(xj) if d >= "2014-06-01" and all(d in s for s in sec.values())]

    def state(d):
        j = bisect.bisect_right(ak, d) - 1
        v = vix[vk[bisect.bisect_left(vk, d) - 1]]
        if v >= 25:
            return "panic"
        if j >= 63:
            c = au2[ak[j]] - au2[ak[j - 63]]
            if c < -.5:
                return "cut"
            if c > .5:
                return "hike"
        return "calm" if v < 15 else "normal"
    ins = defaultdict(lambda: defaultdict(list))
    for i in range(70, len(days) - 20, 5):
        d = days[i]
        if not ("2015" <= d < study.SPLIT):
            continue
        st = state(d)
        m = xj[days[i + 20]] / xj[d] - 1
        for k, s in sec.items():
            ins[st][k].append(s[days[i + 20]] / s[d] - 1 - m)
    picks = {st: [k for k, _ in sorted(((k, sum(v) / len(v)) for k, v in ks.items()), key=lambda x: -x[1])[:2]] for st, ks in ins.items()}
    # out of sample, weekly rebalance
    weekly, prev, ex_by_state = [], None, defaultdict(list)
    for i in range(70, len(days) - 5, 5):
        d = days[i]
        if d < study.SPLIT:
            continue
        st = state(d)
        pk = picks.get(st, [])
        m = xj[days[i + 5]] / xj[d] - 1
        r = .5 * m + sum(.25 * (sec[k][days[i + 5]] / sec[k][d] - 1) for k in pk) + (.25 * (2 - len(pk))) * m
        turn = 0 if prev is None or prev == tuple(pk) else .5
        ex = r - m - .001 * turn
        prev = tuple(pk)
        weekly.append(ex)
        ex_by_state[st].append(ex)
    n = len(weekly)
    mu = sum(weekly) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in weekly) / (n - 1))
    out = {"picks_from_2015_21": picks, "oos_weeks": n, "oos_excess_per_year": 100 * mu * 52, "t": mu / (sd / math.sqrt(n)),
           "by_state": {k: {"weeks": len(v), "excess_per_week": 100 * sum(v) / len(v)} for k, v in ex_by_state.items()},
           "in_sample_table": {st: {k: round(100 * sum(v) / len(v), 2) for k, v in ks.items()} for st, ks in ins.items()}}
    p = Path(os.environ.get("STUDY27_OUT", study.DATA / "study27_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
