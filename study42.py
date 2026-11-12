"""Round 42: does market breadth add anything to the ETF plan (round 25)?
Plan C (60% base, 100% when boosted, 30% after a hawkish RBA surprise) with and without a breadth boost
(breadth < 40% -> boost for 20 sessions). Accumulation index, cash at the RBA rate, 0.1% per turnover."""
import bisect
import json
import math
import os
from pathlib import Path

import study
import study4
import study7
from study25 import stats


def breadth_series(days):
    want = set(days[::5])
    above = {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 260:
            continue
        idx = {r[0]: k for k, r in enumerate(rows)}
        pre = [0.0]
        for r in rows:
            pre.append(pre[-1] + r[4])
        for d in want:
            i = idx.get(d)
            if i is None or i < 200:
                continue
            if sorted(rows[k][6] * rows[k][5] for k in range(i - 19, i + 1))[10] < 1e6:
                continue
            a = above.setdefault(d, [0, 0])
            a[0] += rows[i][4] > (pre[i + 1] - pre[i - 199]) / 200
            a[1] += 1
    return {d: 100 * a / n for d, (a, n) in above.items() if n >= 100}


def main():
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
    br = breadth_series(days)
    bk = sorted(br)
    out = {}
    for use_breadth in (False, True):
        left = {"FEAR": 0, "CUTS": 0, "STREAK": 0, "BREADTH": 0}
        cut_left, prev_w, rets, dates = 0, None, [], []
        for i in range(70, len(days)):
            d, p = days[i], days[i - 1]
            j = bisect.bisect_left(vk, p) - 1
            v = vix[vk[j]] if j >= 0 else None
            if (v and v >= 25) or px[p] / px[days[i - 64]] - 1 < -.05:
                left["FEAR"] = 60
            yj = bisect.bisect_right(yk, p) - 1
            if yj >= 63 and f2[yk[yj]] - f2[yk[yj - 63]] < -.5:
                left["CUTS"] = 20
            if px[p] < px[days[i - 2]] < px[days[i - 3]] < px[days[i - 4]]:
                left["STREAK"] = 5
            bj = bisect.bisect_right(bk, p) - 1
            if use_breadth and bj >= 0 and br[bk[bj]] < 40:
                left["BREADTH"] = 20
            if p in rba and p in f2 and yj >= 1 and f2[p] - f2[yk[yj - 1]] >= .06:
                cut_left = 5
            boosted = any(x > 0 for x in left.values())
            w = .3 if cut_left > 0 else 1.0 if boosted else .6
            if d >= "2015-01-01":
                r = acc[d] / acc[p] - 1
                c = f1[ck[bisect.bisect_right(ck, p) - 1]] / 100 / 252
                rets.append(w * r + (1 - w) * c - (.001 * abs(w - prev_w) if prev_w is not None else 0))
                dates.append(d)
                prev_w = w
            for k in left:
                left[k] = max(0, left[k] - 1)
            cut_left = max(0, cut_left - 1)
        sp = next(k for k, d in enumerate(dates) if d >= study.SPLIT)
        out["含市场宽度" if use_breadth else "不含市场宽度"] = {"all": stats(rets), "2015–21": stats(rets[:sp]), "2022后": stats(rets[sp:])}
    p = Path(os.environ.get("STUDY42_OUT", study.DATA / "study42_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
