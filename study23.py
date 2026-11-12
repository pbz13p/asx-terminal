"""Round 23: robustness of the commodity catch-up signal (round 17).

For each theme: every time the basket's 20-day residual z first drops below -1 (no new entry for 20
days after), record the next 5 / 20-day residual return (basket vs ASX 200, minus beta x commodity) and
the plain basket-vs-ASX-200 return (what a trader actually earns relative to the index). Report the
number of independent entries, mean, median, share positive, simple t-stat, and per-year results.
Also z < -1.5 and the 'leader' side (z > 1).
"""
import bisect
import json
import math
import os
from pathlib import Path

import study
from study17 import THEMES


def stats(xs):
    n = len(xs)
    if n < 3:
        return {"n": n}
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return {"n": n, "mean": 100 * m, "median": 100 * sorted(xs)[n // 2], "pos": 100 * sum(1 for x in xs if x > 0) / n,
            "t": m / (sd / math.sqrt(n)) if sd else None}


def main():
    aud = {r[0]: r[4] for r in study.load("AUDUSD.FOREX")}
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    adays = sorted(acc)
    ak = sorted(aud)
    out = {}
    for theme, (etf, members) in THEMES.items():
        e = {r[0]: r[4] for r in study.load(etf)}
        ek = sorted(e)
        mem = {m: {r[0]: r[4] for r in study.load(f"{m}.AU")} for m in members}
        b, u, dates = [], [], []
        for i in range(1, len(adays)):
            d, p = adays[i], adays[i - 1]
            if d < "2015-01-01":
                continue
            j, k = bisect.bisect_left(ek, d) - 1, bisect.bisect_left(ak, d) - 1
            j0, k0 = bisect.bisect_left(ek, p) - 1, bisect.bisect_left(ak, p) - 1
            if min(j, k, j0, k0) < 0:
                continue
            rs = [s[d] / s[p] - 1 for s in mem.values() if d in s and p in s and s[p] > 0]
            if len(rs) < 3:
                continue
            b.append(sum(rs) / len(rs) - (acc[d] / acc[p] - 1))
            u.append((e[ek[j]] / aud[ak[k]]) / (e[ek[j0]] / aud[ak[k0]]) - 1)
            dates.append(d)
        n = len(b)
        mb, mu = sum(b) / n, sum(u) / n
        bt = sum((x - mu) * (y - mb) for x, y in zip(u, b)) / sum((x - mu) ** 2 for x in u)
        cb = [1.0]
        cu = [1.0]
        for x, y in zip(b, u):
            cb.append(cb[-1] * (1 + x))
            cu.append(cu[-1] * (1 + y))
        res = {i: (cb[i] / cb[i - 20] - 1) - bt * (cu[i] / cu[i - 20] - 1) for i in range(20, n + 1)}
        sd = math.sqrt(sum(v * v for v in res.values()) / len(res))
        th = {}
        for label, cond in (("落后 z<-1", lambda z: z < -1), ("落后 z<-1.5", lambda z: z < -1.5), ("领先 z>1", lambda z: z > 1)):
            last_entry = -999
            f5, f20, raw20, years = [], [], [], {}
            for i in range(20, n - 20):
                z = res[i] / sd
                if cond(z) and i - last_entry > 20:
                    last_entry = i
                    r5 = (cb[i + 5] / cb[i] - 1) - bt * (cu[i + 5] / cu[i] - 1)
                    r20 = (cb[i + 20] / cb[i] - 1) - bt * (cu[i + 20] / cu[i] - 1)
                    plain = cb[i + 20] / cb[i] - 1
                    f5.append(r5)
                    f20.append(r20)
                    raw20.append(plain)
                    years.setdefault(dates[i - 1][:4], []).append(round(100 * plain, 1))
            th[label] = {"resid5": stats(f5), "resid20": stats(f20), "vs_index20": stats(raw20), "years": years}
        out[theme] = th
    p = Path(os.environ.get("STUDY23_OUT", study.DATA / "study23_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
