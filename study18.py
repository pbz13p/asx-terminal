"""Round 18: pairs among look-alike blue chips (banks, miners, supermarkets, insurers, gold, telco).

For each pair: log price ratio, 60-day rolling mean/sd -> z. Enter when |z| > 2 (long the laggard,
short the leader), exit when z crosses 0 or after 20 trading days. Cost 0.1% per leg each way
(0.4% per round trip on the pair). Also a long-only version: buy only the laggard, judged against
holding both equally. Both periods.
"""
import json
import math
import os
from itertools import combinations
from pathlib import Path

import study

GROUPS = {"四大行": ["CBA", "WBC", "NAB", "ANZ"], "铁矿三巨头": ["BHP", "RIO", "FMG"], "超市": ["WOW", "COL"],
          "保险": ["QBE", "IAG", "SUN"], "金矿": ["NST", "EVN"], "电信/基建": ["TLS", "TPG"], "油气": ["WDS", "STO"]}
COST_LEG = 0.001


def main():
    syms = sorted({f"{c}.AU" for g in GROUPS.values() for c in g})
    study.download(syms)
    px = {s[:-3]: {r[0]: r[4] for r in study.load(s)} for s in syms}
    out = {}
    tot = {"all": [], "2015–21": [], "2022后": []}
    lo_tot = {"all": [], "2015–21": [], "2022后": []}
    for gname, codes in GROUPS.items():
        for a, b in combinations(codes, 2):
            A, B = px[a], px[b]
            days = [d for d in sorted(set(A) & set(B)) if d >= "2014-06-01"]
            if len(days) < 500:
                continue
            lr = [math.log(A[d] / B[d]) for d in days]
            trades, lo = [], []
            i = 60
            while i < len(days) - 1:
                w = lr[i - 60:i]
                m = sum(w) / 60
                sd = math.sqrt(sum((x - m) ** 2 for x in w) / 60)
                z = (lr[i] - m) / sd if sd else 0
                if abs(z) > 2 and days[i] >= "2015-01-01":
                    sign = -1 if z > 0 else 1  # z>0: A rich -> short A long B
                    j = i
                    while j < min(len(days) - 1, i + 20):
                        j += 1
                        zj = (lr[j] - m) / sd
                        if (z > 0 and zj <= 0) or (z < 0 and zj >= 0):
                            break
                    ra = A[days[j]] / A[days[i]] - 1
                    rb = B[days[j]] / B[days[i]] - 1
                    pair = sign * (ra - rb) / 2 - 4 * COST_LEG / 2  # half capital per leg
                    lag, lead = (rb, ra) if z > 0 else (ra, rb)
                    lo_ex = lag - (ra + rb) / 2 - 2 * COST_LEG  # long laggard vs holding both
                    per = "2015–21" if days[i] < study.SPLIT else "2022后"
                    trades.append((per, pair, j - i))
                    lo.append((per, lo_ex))
                    i = j + 1
                else:
                    i += 1
            if not trades:
                continue
            key = f"{a}/{b}"
            mean = lambda xs: 100 * sum(xs) / len(xs) if xs else None
            res = {"group": gname, "n": len(trades), "avg": mean([t[1] for t in trades]),
                   "win": 100 * sum(1 for t in trades if t[1] > 0) / len(trades),
                   "days": sum(t[2] for t in trades) / len(trades), "long_only": mean([x[1] for x in lo])}
            for per in ("2015–21", "2022后"):
                res[per] = mean([t[1] for t in trades if t[0] == per])
                res[per + "_lo"] = mean([x[1] for x in lo if x[0] == per])
            out[key] = res
            for t in trades:
                tot["all"].append(t[1]); tot[t[0]].append(t[1])
            for x in lo:
                lo_tot["all"].append(x[1]); lo_tot[x[0]].append(x[1])
    summary = {k: {"n": len(v), "avg": 100 * sum(v) / len(v) if v else None,
                   "win": 100 * sum(1 for x in v if x > 0) / len(v) if v else None} for k, v in tot.items()}
    lo_summary = {k: {"n": len(v), "avg": 100 * sum(v) / len(v) if v else None,
                      "win": 100 * sum(1 for x in v if x > 0) / len(v) if v else None} for k, v in lo_tot.items()}
    p = Path(os.environ.get("STUDY18_OUT", study.DATA / "study18_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pairs": out, "summary": summary, "long_only": lo_summary}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"summary": summary, "long_only": lo_summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
