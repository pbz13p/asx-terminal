"""Round 31: is the hawkish-RBA rule knife-edge? Plan D (halve the ETF for N sessions after an RBA day with
2-year yield up >= X bp) across thresholds X and holding windows N, full period and 2022+."""
import bisect
import json
import math
import os
from pathlib import Path

import study
import study4
import study7


def main():
    f2 = study7.rba_table("f2")["FCMYGBAG2D"]
    yk = sorted(f2)
    f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
    ck = sorted(f1)
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    days = [d for d in sorted(acc) if d >= "2014-12-01"]
    rba = set(study4.fallback_events()["澳联储 利率决议"])
    dy = {}
    for d in rba:
        if d in f2:
            k = yk.index(d)
            if k:
                dy[d] = f2[d] - f2[yk[k - 1]]
    cg = lambda rs: 100 * (math.prod(1 + x for x in rs) ** (252 / len(rs)) - 1)
    out = {}
    for x in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15):
        for n in (3, 5, 10):
            for start in ("2015-01-01", study.SPLIT):
                bh, pl, left = [], [], 0
                for i in range(1, len(days)):
                    d, p = days[i], days[i - 1]
                    if p in dy and dy[p] >= x:
                        left = n
                    if d >= start:
                        r = acc[d] / acc[p] - 1
                        c = f1[ck[bisect.bisect_right(ck, p) - 1]] / 100 / 252
                        w = .5 if left > 0 else 1.0
                        bh.append(r)
                        pl.append(w * r + (1 - w) * c - (.0005 if left in (n, 1) else 0))
                    left = max(0, left - 1)
                out[f"≥{int(x * 100)}bp·{n}天·{'全期' if start < '2016' else '2022后'}"] = round(cg(pl) - cg(bh), 2)
    p = Path(os.environ.get("STUDY31_OUT", study.DATA / "study31_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
