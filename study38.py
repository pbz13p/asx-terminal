"""Round 38: overnight US bond and currency shocks -> ASX next day and next week.

US 10y yield (FRED DGS10) moves >= +/-10bp in a US session, and AUD/USD moves >= +/-1.2% in a day.
For the next ASX session (close-to-close, so the open gap is included) and the following week (5 sessions
from that close — the tradeable part), report ASX 200 and sector moves. Both periods.
"""
import bisect
import json
import os
from collections import defaultdict
from pathlib import Path

import study
import study7
from study9 import SECTORS


def main():
    study.download(["AXJO.INDX", "AUDUSD.FOREX"] + list(SECTORS.values()))
    xj = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    sec = {k: {r[0]: r[4] for r in study.load(v)} for k, v in SECTORS.items()}
    aud = {r[0]: r[4] for r in study.load("AUDUSD.FOREX")}
    us10 = study7.fred("DGS10")
    days = [d for d in sorted(xj) if d >= "2015-01-01"]
    uk, ak = sorted(us10), sorted(aud)
    idx = {d: i for i, d in enumerate(days)}
    res = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for i in range(1, len(days) - 6):
        d = days[i]
        j = bisect.bisect_left(uk, d) - 1  # last US session before this ASX session
        k = bisect.bisect_left(ak, d) - 1
        tags = []
        if j >= 1:
            dy = us10[uk[j]] - us10[uk[j - 1]]
            if dy >= .10:
                tags.append("美10年收益率隔夜涨≥10bp")
            elif dy <= -.10:
                tags.append("美10年收益率隔夜跌≥10bp")
        if k >= 1:
            da = aud[ak[k]] / aud[ak[k - 1]] - 1
            if da >= .012:
                tags.append("澳元单日涨≥1.2%")
            elif da <= -.012:
                tags.append("澳元单日跌≥1.2%")
        if not tags:
            continue
        p = days[i - 1]
        per = "2015–21" if d < study.SPLIT else "2022后"
        day = xj[d] / xj[p] - 1
        wk = xj[days[i + 5]] / xj[d] - 1
        for t in tags:
            for g in ("all", per):
                r = res[t][g]
                r["day"].append(day)
                r["wk"].append(wk)
                for s, ss in sec.items():
                    if d in ss and p in ss and days[i + 5] in ss:
                        r["d_" + s].append(ss[d] / ss[p] - 1 - day)
                        r["w_" + s].append(ss[days[i + 5]] / ss[d] - 1 - wk)
    mean = lambda a: round(100 * sum(a) / len(a), 3) if a else None
    out = {t: {g: {"n": len(r["day"]), "asx_day": mean(r["day"]), "asx_next_week": mean(r["wk"]),
                   "up_week": round(100 * sum(1 for x in r["wk"] if x > 0) / len(r["wk"])) if r["wk"] else None,
                   "sector_day_vs_asx": {s: mean(r["d_" + s]) for s in sec},
                   "sector_week_vs_asx": {s: mean(r["w_" + s]) for s in sec}} for g, r in gs.items()} for t, gs in res.items()}
    pth = Path(os.environ.get("STUDY38_OUT", study.DATA / "study38_results.json"))
    pth.parent.mkdir(parents=True, exist_ok=True)
    pth.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
