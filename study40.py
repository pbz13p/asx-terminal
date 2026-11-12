"""Round 40: unusual volume with little price change ('quiet accumulation / distribution').

Event: volume >= 5x its 20-day median while the close moved less than 2% (liquid stocks, 20-day median
turnover >= $1M). Split by whether the close was in the top or bottom third of the day's range. Next 5 /
20 / 60 sessions from the event close, vs the ASX 200 accumulation index; month-clustered t; both periods.
Block trades and index rebalances also show up here, so expect noise.
"""
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    agg = defaultdict(lambda: defaultdict(lambda: {"x": [], "m": defaultdict(list)}))
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        for i in range(21, len(rows) - 61):
            d, o, h, l, c, v, raw = rows[i]
            if d < "2015-01-01" or raw < .2 or d not in acc:
                continue
            pc = rows[i - 1][4]
            vols = sorted(r[5] for r in rows[i - 20:i])
            turn = sorted(r[6] * r[5] for r in rows[i - 20:i])[10]
            if pc <= 0 or vols[10] <= 0 or turn < 1e6 or v < 5 * vols[10] or abs(c / pc - 1) >= .02 or h <= l:
                continue
            pos = (c - l) / (h - l)
            kind = "放量不涨不跌·收在高位" if pos >= 2 / 3 else "放量不涨不跌·收在低位" if pos <= 1 / 3 else "放量不涨不跌·收在中间"
            per = "2015–21" if d < study.SPLIT else "2022后"
            for hd in (5, 20, 60):
                e = rows[i + hd][0]
                if e not in acc:
                    continue
                ex = rows[i + hd][4] / c - 1 - (acc[e] / acc[d] - 1)
                for k in (kind, f"{kind}|{per}", "全部"):
                    agg[k][hd]["x"].append(ex)
                    agg[k][hd]["m"][d[:7]].append(ex)
    out = {}
    for k, hs in agg.items():
        out[k] = {}
        for h, a in hs.items():
            ms = [sum(v) / len(v) for v in a["m"].values()]
            mm = sum(ms) / len(ms)
            sd = math.sqrt(sum((x - mm) ** 2 for x in ms) / max(1, len(ms) - 1))
            out[k][str(h)] = {"n": len(a["x"]), "ex": round(100 * sum(a["x"]) / len(a["x"]), 2),
                              "median": round(100 * sorted(a["x"])[len(a["x"]) // 2], 2),
                              "t": round(mm / (sd / math.sqrt(len(ms))), 2) if sd and len(ms) > 5 else None}
    p = Path(os.environ.get("STUDY40_OUT", study.DATA / "study40_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
