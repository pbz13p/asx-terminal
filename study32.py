"""Round 32: takeover / scheme arbitrage on the ASX (bid premium capture).

Detect likely takeover announcements without a news feed:
  - gap up >= 20% on >= 10x the 20-day median volume, and
  - over the next 10 sessions the stock barely moves (daily volatility < 1/3 of its prior 60-day level):
    a price pinned near an offer.
Entry: close 10 sessions after the jump (deal known, price settled). Exit: the last trade before the stock
delists (scheme completed, cash paid near that price), or after 300 sessions if still listed.
Measures per deal: return, holding days, annualised return, worst drawdown (deal-break risk).
"""
import json
import math
import os
from pathlib import Path

import study


def main():
    deals = []
    last_day = max(r[0] for r in study.load("AXJO.INDX"))
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        n = len(rows)
        if n < 100:
            continue
        code = f.name[:-8]
        i = 61
        while i < n - 11:
            d, o, h, l, c, v, raw = rows[i]
            pc = rows[i - 1][4]
            vols = sorted(r[5] for r in rows[i - 20:i])
            if pc <= 0 or vols[10] <= 0 or raw < .1 or c / pc - 1 < .20 or v < 10 * vols[10] or d < "2015-01-01":
                i += 1
                continue
            pre = [math.log(rows[k][4] / rows[k - 1][4]) for k in range(i - 59, i) if rows[k - 1][4] > 0]
            post = [math.log(rows[k][4] / rows[k - 1][4]) for k in range(i + 1, i + 11) if rows[k - 1][4] > 0]
            sd = lambda a: math.sqrt(sum(x * x for x in a) / len(a)) if a else 0
            if not pre or not post or sd(post) >= sd(pre) / 3:
                i += 1
                continue
            e_i = i + 10
            entry = rows[e_i][4]
            end_i = min(n - 1, e_i + 300)
            delisted = rows[-1][0] < last_day and n - 1 <= e_i + 300
            exit_ = rows[end_i][4]
            worst = min(r[4] for r in rows[e_i:end_i + 1]) / entry - 1
            hold = end_i - e_i
            r = exit_ / entry - 1
            deals.append({"code": code, "date": d, "jump": round(100 * (c / pc - 1), 1), "ret": round(100 * r, 2),
                          "days": hold, "worst": round(100 * worst, 1), "completed": delisted,
                          "ann": round(100 * ((1 + r) ** (252 / max(hold, 20)) - 1), 1),
                          "turnover": round(sorted(rr[6] * rr[5] for rr in rows[e_i - 5:e_i + 1])[3])})
            i = end_i + 1
    comp = [x for x in deals if x["completed"]]
    still = [x for x in deals if not x["completed"]]
    fail = [x for x in deals if x["worst"] <= -15]
    mean = lambda a, k: sum(x[k] for x in a) / len(a) if a else None
    med = lambda a, k: sorted(x[k] for x in a)[len(a) // 2] if a else None
    out = {"deals": len(deals), "completed_delisted": len(comp), "still_listed_or_broke": len(still),
           "break_share_worst_le_-15": 100 * len(fail) / len(deals) if deals else None,
           "completed": {"ret_mean": mean(comp, "ret"), "ret_median": med(comp, "ret"), "days_median": med(comp, "days"),
                         "ann_median": med(comp, "ann")},
           "not_completed": {"ret_mean": mean(still, "ret"), "ret_median": med(still, "ret")},
           "all": {"ret_mean": mean(deals, "ret"), "ret_median": med(deals, "ret"), "win": 100 * sum(1 for x in deals if x["ret"] > 0) / len(deals) if deals else None},
           "by_period": {p: {"n": len(a), "ret_mean": mean(a, "ret"), "ret_median": med(a, "ret")} for p, a in
                         (("2015–21", [x for x in deals if x["date"] < study.SPLIT]), ("2022后", [x for x in deals if x["date"] >= study.SPLIT]))},
           "recent": sorted(deals, key=lambda x: x["date"])[-25:]}
    p = Path(os.environ.get("STUDY32_OUT", study.DATA / "study32_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "recent"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
