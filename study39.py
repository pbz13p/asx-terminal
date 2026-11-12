"""Round 39: robustness of the gold-miner catch-up rule (rounds 17/23): thresholds x horizons x periods,
independent entries only, return = gold basket vs ASX 200 accumulation (what a trader earns vs the index)."""
import bisect
import json
import math
import os
from pathlib import Path

import study
from study17 import THEMES


def main():
    etf, members = THEMES["黄金"]
    aud = {r[0]: r[4] for r in study.load("AUDUSD.FOREX")}
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    e = {r[0]: r[4] for r in study.load(etf)}
    ek, ak, adays = sorted(e), sorted(aud), sorted(acc)
    mem = {m: {r[0]: r[4] for r in study.load(f"{m}.AU")} for m in members}
    b, u, dates = [], [], []
    for i in range(1, len(adays)):
        d, p = adays[i], adays[i - 1]
        if d < "2015-01-01":
            continue
        j, k, j0, k0 = (bisect.bisect_left(ek, d) - 1, bisect.bisect_left(ak, d) - 1,
                        bisect.bisect_left(ek, p) - 1, bisect.bisect_left(ak, p) - 1)
        rs = [s[d] / s[p] - 1 for s in mem.values() if d in s and p in s and s[p] > 0]
        if min(j, k, j0, k0) < 0 or len(rs) < 3:
            continue
        b.append(sum(rs) / len(rs) - (acc[d] / acc[p] - 1))
        u.append((e[ek[j]] / aud[ak[k]]) / (e[ek[j0]] / aud[ak[k0]]) - 1)
        dates.append(d)
    n = len(b)
    out = {}
    for look in (10, 20, 40):
        # beta and residual sd estimated on data before 2019 only, applied after (avoids look-ahead)
        cut = next(i for i, d in enumerate(dates) if d >= "2019-01-01")
        mb, mu = sum(b[:cut]) / cut, sum(u[:cut]) / cut
        bt = sum((x - mu) * (y - mb) for x, y in zip(u[:cut], b[:cut])) / sum((x - mu) ** 2 for x in u[:cut])
        cb, cu = [1.0], [1.0]
        for x, y in zip(b, u):
            cb.append(cb[-1] * (1 + x)); cu.append(cu[-1] * (1 + y))
        res = [None] * (n + 1)
        for i in range(look, n + 1):
            res[i] = (cb[i] / cb[i - look] - 1) - bt * (cu[i] / cu[i - look] - 1)
        sd = math.sqrt(sum(r * r for r in res[look:cut + 1]) / (cut + 1 - look))
        for thr in (-0.75, -1.0, -1.25, -1.5):
            for hold in (10, 20, 40):
                last, xs = -999, []
                for i in range(cut, n - hold):
                    if res[i] is not None and res[i] / sd < thr and i - last > hold:
                        last = i
                        xs.append(cb[i + hold] / cb[i] - 1)
                if len(xs) >= 3:
                    m = sum(xs) / len(xs)
                    s2 = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
                    out[f"回看{look}天·z<{thr}·持{hold}天"] = {"n": len(xs), "mean": round(100 * m, 2),
                                                          "pos": round(100 * sum(1 for x in xs if x > 0) / len(xs)),
                                                          "t": round(m / (s2 / math.sqrt(len(xs))), 2) if s2 else None}
    p = Path(os.environ.get("STUDY39_OUT", study.DATA / "study39_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
