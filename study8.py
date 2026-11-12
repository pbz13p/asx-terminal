"""Round 8: robustness checks for the two strongest macro signals from round 7.

1. RBA hawkish surprise -> next-week ASX 200: permutation test (shuffle the surprise labels across
   meetings 5000 times) and the same test on 2/10-day windows.
2. AU 2y yield down >0.5% over 3 months -> next-month ASX 200: list independent episodes, drop one
   episode at a time, and compare with random weeks of the same count.
"""
import json
import os
import random
from pathlib import Path

import study
import study4
import study7


def main():
    random.seed(7)
    f2 = study7.rba_table("f2")
    au2 = f2["FCMYGBAG2D"]
    xj = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    if not xj:
        study.download(["AXJO.INDX"])
        xj = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    days = sorted(xj)
    keys = sorted(au2)
    cal = study4.fallback_events()["澳联储 利率决议"]
    out = {"rba": {}, "au2": {}}
    meet = []
    for d in sorted(cal):
        if d < "2015-01-01" or d not in xj:
            continue
        dy = study7.change_on(au2, keys, d)
        i = days.index(d)
        if dy is None or i + 11 >= len(days):
            continue
        meet.append((d, dy, {h: xj[days[i + h]] / xj[d] - 1 for h in (2, 5, 10)}))
    meet.sort(key=lambda m: m[1])
    n3 = len(meet) // 3
    for h in (2, 5, 10):
        hawk = [m[2][h] for m in meet[-n3:]]
        dove = [m[2][h] for m in meet[:n3]]
        obs = sum(dove) / n3 - sum(hawk) / n3
        allr = [m[2][h] for m in meet]
        cnt = 0
        for _ in range(5000):
            random.shuffle(allr)
            if sum(allr[:n3]) / n3 - sum(allr[-n3:]) / n3 >= obs:
                cnt += 1
        out["rba"][str(h)] = {"hawk": 100 * sum(hawk) / n3, "dove": 100 * sum(dove) / n3, "spread": 100 * obs,
                              "p": cnt / 5000, "n": n3}
    # AU 2y regime
    sig = []
    for i in range(70, len(days) - 20, 5):
        d = days[i]
        if d < "2015-01-01":
            continue
        import bisect
        j = bisect.bisect_right(keys, d) - 1
        if j < 63:
            continue
        if au2[keys[j]] - au2[keys[j - 63]] < -.5:
            sig.append((d, xj[days[i + 20]] / xj[d] - 1))
    eps = []
    for d, r in sig:
        if eps and (study7.datetime.fromisoformat(d) - study7.datetime.fromisoformat(eps[-1]["last"])).days <= 90:
            eps[-1]["r"].append(r)
            eps[-1]["last"] = d
        else:
            eps.append({"first": d, "last": d, "r": [r]})
    allw = [xj[days[i + 20]] / xj[days[i]] - 1 for i in range(70, len(days) - 20, 5) if days[i] >= "2015-01-01"]
    base = sum(allw) / len(allw)
    mean_sig = sum(r for _, r in sig) / len(sig)
    loo = []
    for k in range(len(eps)):
        rest = [r for e2 in eps[:k] + eps[k + 1:] for r in e2["r"]]
        loo.append(100 * sum(rest) / len(rest) if rest else None)
    cnt = 0
    for _ in range(5000):
        smp = random.sample(allw, len(sig))
        if sum(smp) / len(smp) >= mean_sig:
            cnt += 1
    out["au2"] = {"n": len(sig), "mean": 100 * mean_sig, "base": 100 * base, "p_random": cnt / 5000,
                  "episodes": [{"from": e["first"], "to": e["last"], "n": len(e["r"]), "mean": 100 * sum(e["r"]) / len(e["r"])} for e in eps],
                  "leave_one_out": loo}
    p = Path(os.environ.get("STUDY8_OUT", study.DATA / "study8_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
