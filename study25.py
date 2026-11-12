"""Round 25: combine the index-level rules that survived testing into one ETF plan, measured honestly.

Instrument: ASX 200 accumulation index (an ETF like IOZ/STW incl. dividends). Idle cash earns the RBA
cash rate. 0.1% cost per unit of turnover. Signals use only data known at the previous close.
Rules (all fixed in earlier rounds, nothing tuned here):
  FEAR    VIX >= 25 or ASX 200 down > 5% over 3 months  -> 'boost' for 60 sessions
  CUTS    AU 2y yield down > 0.5% over 3 months          -> 'boost' for 20 sessions
  STREAK  3 down closes in a row                          -> 'boost' for 5 sessions
  HAWK    RBA decision day with 2y yield up >= 6bp        -> 'cut' for 5 sessions
Plans:
  A  always 100%
  B  80% base, 100% when boosted, 50% when cut
  C  60% base, 100% when boosted, 30% when cut
  D  100% base, 50% when cut only
"""
import bisect
import json
import math
import os
from pathlib import Path

import study
import study4
import study7

COST = 0.001


def stats(rets):
    eq = peak = 1.0
    dd = 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))
    return {"cagr": 100 * (eq ** (252 / len(rets)) - 1), "maxdd": 100 * dd, "sharpe": m / sd * math.sqrt(252) if sd else 0, "vol": 100 * sd * math.sqrt(252)}


def main():
    study.download(["AXJOA.INDX", "AXJO.INDX", "VIX.INDX"])
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    px = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    vix = {r[0]: r[4] for r in study.load("VIX.INDX")}
    vk = sorted(vix)
    f2 = study7.rba_table("f2")["FCMYGBAG2D"]
    yk = sorted(f2)
    f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
    ck = sorted(f1)
    rba = set(study4.fallback_events()["澳联储 利率决议"])
    days = [d for d in sorted(acc) if d in px and d >= "2014-06-01"]
    start = next(i for i, d in enumerate(days) if d >= "2015-01-01")
    boost_left = {"FEAR": 0, "CUTS": 0, "STREAK": 0}
    cut_left = 0
    plans = {"A 一直 100%": (1.0, 1.0, 1.0), "B 平时 80%": (0.8, 1.0, 0.5), "C 平时 60%": (0.6, 1.0, 0.3), "D 只在鹰派后减仓": (1.0, 1.0, 0.5)}
    w_prev = {k: None for k in plans}
    rets = {k: [] for k in plans}
    dates = []
    counts = {"FEAR": 0, "CUTS": 0, "STREAK": 0, "HAWK": 0}
    for i in range(70, len(days)):
        d, p = days[i], days[i - 1]
        # signals known at the close of p
        j = bisect.bisect_left(vk, p) - 1
        v = vix[vk[j]] if j >= 0 else None
        x3 = px[p] / px[days[i - 64]] - 1
        if (v and v >= 25) or x3 < -.05:
            boost_left["FEAR"] = 60
        yj = bisect.bisect_right(yk, p) - 1
        if yj >= 63 and f2[yk[yj]] - f2[yk[yj - 63]] < -.5:
            boost_left["CUTS"] = 20
        if px[p] < px[days[i - 2]] < px[days[i - 3]] < px[days[i - 4]]:
            boost_left["STREAK"] = 5
        if p in rba and p in f2 and yj >= 1 and f2[p] - f2[yk[yj - 1]] >= .06:
            cut_left = 5
        boosted = any(v_ > 0 for v_ in boost_left.values())
        cut = cut_left > 0
        if i >= start:
            r = acc[d] / acc[p] - 1
            c = f1[ck[bisect.bisect_right(ck, p) - 1]] / 100 / 252 if ck else 0
            for k, (base, up, down) in plans.items():
                w = down if cut else up if boosted else base
                turn = abs(w - w_prev[k]) if w_prev[k] is not None else 0
                rets[k].append(w * r + (1 - w) * c - COST * turn)
                w_prev[k] = w
            dates.append(d)
            for k_ in boost_left:
                counts[k_] += boost_left[k_] > 0
            counts["HAWK"] += cut
        for k_ in boost_left:
            boost_left[k_] = max(0, boost_left[k_] - 1)
        cut_left = max(0, cut_left - 1)
    sp = next(k for k, d in enumerate(dates) if d >= study.SPLIT)
    out = {k: {"all": stats(v), "2015–21": stats(v[:sp]), "2022后": stats(v[sp:]),
               "avg_weight": None} for k, v in rets.items()}
    out["_days_active"] = {k: round(100 * v / len(dates), 1) for k, v in counts.items()}
    p = Path(os.environ.get("STUDY25_OUT", study.DATA / "study25_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
