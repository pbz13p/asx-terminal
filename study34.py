"""Round 34: placement / halt-reopen-down rule for liquid stocks, judged against the ASX 200 (like round 33)."""
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
import study2


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    xdays = [r[0] for r in study.load("AXJO.INDX")]
    xidx = {d: k for k, d in enumerate(xdays)}
    agg = defaultdict(lambda: defaultdict(lambda: {"ex": [], "m": defaultdict(list)}))
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        n = len(rows)
        for j in range(25, n - 61):
            pre = j - 1
            while pre > 0 and rows[pre][5] == 0 and j - pre <= 6:
                pre -= 1
            d0, d1 = rows[pre][0], rows[j][0]
            missed = (xidx[d1] - xidx[d0] - 1) if d0 in xidx and d1 in xidx else 0
            halted = missed + (j - 1 - pre)
            if not (1 <= halted <= 5) or rows[j][5] == 0 or pre < 22 or d1 < "2015-01-01":
                continue
            d, o, h, l, c, v, raw = rows[j]
            pc = rows[pre][4]
            vols = sorted(r[5] for r in rows[pre - 20:pre + 1])
            turn = sorted(r[6] * r[5] for r in rows[pre - 20:pre + 1])[10]
            if pc <= 0 or o <= 0 or vols[10] <= 0 or v < 3 * vols[10] or o / pc - 1 > -.05 or rows[pre][6] < .05:
                continue
            liq = "日成交≥200万" if turn >= 2e6 else "日成交50–200万" if turn >= 5e5 else "日成交<50万"
            per = "2015–21" if d < study.SPLIT else "2022后"
            if d not in acc:
                continue
            for hd in (5, 20, 60):
                e = rows[j + hd][0]
                if e not in acc:
                    continue
                ex = rows[j + hd][4] / c - 1 - (acc[e] / acc[d] - 1)
                for key in (liq, f"{liq}|{per}"):
                    agg[key][hd]["ex"].append(ex)
                    agg[key][hd]["m"][d[:7]].append(ex)
    out = {}
    for k, hs in agg.items():
        out[k] = {}
        for h, a in hs.items():
            ms = [sum(v) / len(v) for v in a["m"].values()]
            mm = sum(ms) / len(ms)
            sd = math.sqrt(sum((x - mm) ** 2 for x in ms) / max(1, len(ms) - 1))
            out[k][str(h)] = {"n": len(a["ex"]), "ex": 100 * sum(a["ex"]) / len(a["ex"]),
                              "median": 100 * sorted(a["ex"])[len(a["ex"]) // 2],
                              "t": mm / (sd / math.sqrt(len(ms))) if sd and len(ms) > 5 else None}
    p = Path(os.environ.get("STUDY34_OUT", study.DATA / "study34_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
