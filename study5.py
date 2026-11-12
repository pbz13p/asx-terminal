"""Round 5: can the macro findings be turned into a simple ASX 200 ETF rule?

Strategies on the ASX 200 price index (signals use only data known before the
trade; US data is from the prior US session; 0.1% cost per unit of turnover):
  BH    buy and hold
  VT    volatility target 12%: weight = min(1, 12% / forecast), forecast =
        average of last-22-day realised vol and VIX; rebalanced weekly
  FEAR  50% invested normally, 100% for 60 trading days after a fear signal
        (VIX >= 25, or S&P 500 or ASX 200 down > 5% over 3 months)
  FEAR+VT  fear rule, then capped by the vol target
  STREAK   100% for 5 days after 3 down days in a row, else 50%
Cash earns nothing (conservative). Results per full period, 2015-2021, 2022+.
"""
import bisect
import json
import math
import os
from pathlib import Path

import study

COST = 0.001


def series(sym):
    return {r[0]: r[4] for r in study.load(sym)}


def stats(rets, days):
    if not rets:
        return None
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    yrs = len(rets) / 252
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
    return {"cagr": 100 * (eq ** (1 / yrs) - 1), "vol": 100 * sd * math.sqrt(252), "maxdd": 100 * dd,
            "sharpe": (m / sd * math.sqrt(252)) if sd else 0, "from": days[0], "to": days[-1]}


def main():
    study.download(["AXJO.INDX", "VIX.INDX", "GSPC.INDX"])
    xj, vix, spx = series("AXJO.INDX"), series("VIX.INDX"), series("GSPC.INDX")
    days = [d for d in sorted(xj) if d >= "2014-06-01"]
    vd, sd = sorted(vix), sorted(spx)

    def prior(s, keys, d, back=0):
        j = bisect.bisect_left(keys, d) - 1 - back  # strictly before the ASX day
        return s[keys[j]] if j >= 0 else None
    r = [0.0] + [xj[days[i]] / xj[days[i - 1]] - 1 for i in range(1, len(days))]
    w = {k: [0.0] * len(days) for k in ("BH", "VT", "FEAR", "FEAR+VT", "STREAK")}
    fear_left, streak_left = 0, 0
    for i in range(70, len(days)):
        d = days[i]
        # signals known at the close of day i-1 (weights apply to the return of day i)
        rv22 = math.sqrt(sum(x * x for x in r[i - 22:i]) / 22 * 252)
        v = prior(vix, vd, d)
        fc = (rv22 + v / 100) / 2 if v else rv22
        s_now, s_back = prior(spx, sd, d), prior(spx, sd, d, 63)
        x3 = xj[days[i - 1]] / xj[days[i - 64]] - 1
        fear = (v and v >= 25) or (s_now and s_back and s_now / s_back - 1 < -.05) or x3 < -.05
        if fear:
            fear_left = 60
        if r[i - 1] < 0 and r[i - 2] < 0 and r[i - 3] < 0:
            streak_left = 5
        vt = min(1.0, .12 / fc) if fc > 0 else 1.0
        if i % 5 and i > 70:
            vt = w["VT"][i - 1]
        w["BH"][i] = 1.0
        w["VT"][i] = vt
        w["FEAR"][i] = 1.0 if fear_left > 0 else .5
        w["FEAR+VT"][i] = min(w["FEAR"][i], max(vt, .5) if fear_left > 0 else vt)
        w["STREAK"][i] = 1.0 if streak_left > 0 else .5
        fear_left = max(0, fear_left - 1)
        streak_left = max(0, streak_left - 1)
    out = {}
    for k, ws in w.items():
        rets = [ws[i] * r[i] - COST * abs(ws[i] - ws[i - 1]) for i in range(71, len(days))]
        dd = days[71:]
        exp = sum(ws[71:]) / len(ws[71:])
        out[k] = {"all": stats(rets, dd), "in": stats([x for x, d in zip(rets, dd) if "2015" <= d < study.SPLIT], [d for d in dd if "2015" <= d < study.SPLIT]),
                  "out": stats([x for x, d in zip(rets, dd) if d >= study.SPLIT], [d for d in dd if d >= study.SPLIT]),
                  "avg_exposure": 100 * exp, "now": ws[-1]}
    p = Path(os.environ.get("STUDY5_OUT", study.DATA / "study5_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
