"""Round 52: do the main avoid-rules still work in 2024-2026 (after the RBA format change, recent regime)?
 (a) results-season gap-down >= 8% on >= 3x volume, small/mid caps: 20-day return vs ASX 200
 (b) halt then reopen >= 5% lower on >= 3x volume, turnover $0.5-2M: 20-day return vs ASX 200
Reported for 2015-21, 2022-23, 2024+."""
import json
import os
from collections import defaultdict
from pathlib import Path

import study
from study10 import season


def per(d):
    return "2015–21" if d < "2022" else "2022–23" if d < "2024" else "2024后"


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    xdays = [r[0] for r in study.load("AXJO.INDX")]
    xidx = {d: k for k, d in enumerate(xdays)}
    res = defaultdict(lambda: defaultdict(list))
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        n = len(rows)
        for i in range(25, n - 21):
            d, o, h, l, c, v, raw = rows[i]
            if d < "2015-01-01" or raw < .05 or d not in acc:
                continue
            pre = i - 1
            while pre > 0 and rows[pre][5] == 0 and i - pre <= 6:
                pre -= 1
            if pre < 21:
                continue
            pc = rows[pre][4]
            vols = sorted(r[5] for r in rows[pre - 20:pre + 1])
            turn = sorted(r[6] * r[5] for r in rows[pre - 20:pre + 1])[10]
            if pc <= 0 or o <= 0 or vols[10] <= 0 or v < 3 * vols[10]:
                continue
            gap = o / pc - 1
            e_d = rows[i + 20][0]
            if e_d not in acc:
                continue
            missed = (xidx[d] - xidx[rows[pre][0]] - 1) if d in xidx and rows[pre][0] in xidx else 0
            halted = missed + (i - 1 - pre)
            if 1 <= halted <= 5 and gap <= -.05 and 5e5 <= turn < 2e6:
                res["停牌复牌低开(中小盘)"][per(d)].append(rows[i + 20][4] / c - 1 - (acc[e_d] / acc[d] - 1))
            if halted == 0 and season(d) and gap <= -.08 and 2e5 <= turn < 2e6 and raw >= .2 and rows[i + 1][1] > 0:
                res["业绩季低开≥8%(中小盘)"][per(d)].append(rows[i + 20][4] / rows[i + 1][1] - 1 - (acc[e_d] / acc[d] - 1))
    out = {k: {p: {"n": len(v), "ex20": round(100 * sum(v) / len(v), 2), "median": round(100 * sorted(v)[len(v) // 2], 2)}
               for p, v in sorted(ps.items())} for k, ps in res.items()}
    pth = Path(os.environ.get("STUDY52_OUT", study.DATA / "study52_results.json"))
    pth.parent.mkdir(parents=True, exist_ok=True)
    pth.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
