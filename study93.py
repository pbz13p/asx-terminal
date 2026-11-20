"""Round 93: does heavy selling show up BEFORE bad news in ASX small caps? (Frank 2026-11-20, CND sold off on ~7x volume
with no announcement.) Rules fixed before results; trials registered: 2 parts x 3 thresholds x 2 horizons = 12.

Universe: every ASX stock 2015 on (delisted included, round-80 consolidation fix), raw price >= 0.5c, 20-day median $
traded >= $5k. Announcements: our archive, price-sensitive only; an announcement belongs to the first trading day on or
after its date; its "reaction" = close of the next trading day / close of the day before - 1 (covers after-close releases).
  bad news  = reaction <= -20%;  good news = reaction >= +20%;  other = everything else.

Part A (look back from the news): for each price-sensitive announcement, the 5 trading days before it:
  volume ratio = their total volume / (5 x average daily volume of days -60..-11), and their price return.
  Reported: median ratio, share with ratio >= 3 / >= 5, share with a pre-move of <= -10%, for bad / good / other.
Part B (look forward from a CND-like day): a day with volume >= 5x the previous 20-day average, close down >= 10%, and no
  announcement that day or the day before.  Outcome over the next 10 / 20 trading days: chance a bad-news announcement
  comes out, chance of a further -20%, median return from the next open. Baseline: all eligible days (1 in 5 sampled),
  and down >= 10% days on normal volume (so we know if the VOLUME adds anything beyond the drop itself).
t of the difference vs baseline clustered by month.
"""
import gzip
import json
import math
import os
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

import numpy as np

import study
import study80

OUT = Path(os.environ.get("STUDY93_OUT", "out/study93_results.json"))
ANN = Path("data/ann")


def ann_days(code):
    f = ANN / f"{code}.json.gz"
    if not f.exists():
        return []
    d = json.loads(gzip.decompress(f.read_bytes()))
    return sorted({it["date"][:10] for it in d.get("listed", []) if it.get("sensitive") and it.get("date")}), \
        sorted({it["date"][:10] for it in d.get("listed", []) if it.get("date")})


def tstat(x, months):
    by = defaultdict(list)
    for v, m in zip(x, months):
        by[m].append(v)
    a = np.array([np.mean(v) for v in by.values()])
    return round(float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a)))), 2) if len(a) > 2 and a.std() > 0 else None


def main():
    cal = [r[0] for r in study.load("AXJO.INDX")]
    A = defaultdict(list)          # bad/good/other -> (ratio, pre_ret)
    B = defaultdict(list)          # group -> dicts
    for f in sorted(study.CACHE.glob("*.AU.json")):
        code = f.name[:-8]
        res = ann_days(code)
        if not res:
            continue
        sens, alld = res
        rows = study.load(f.name[:-5])
        if len(rows) < 120:
            continue
        rows = [r + (r[6] * r[5],) for r in rows]
        rows, _ = study80.fix_consolidations(rows)
        rows = study80.to_calendar(rows, cal)
        d = [r[0] for r in rows]
        o = np.array([r[1] for r in rows], float)
        c = np.array([r[4] for r in rows], float)
        v = np.array([r[5] for r in rows], float)
        raw = np.array([r[6] for r in rows], float)
        val = np.array([r[7] for r in rows], float)
        n = len(d)
        if n < 120:
            continue

        def ok(i):
            return i >= 61 and d[i] >= "2015-01-01" and raw[i] >= 0.005 and np.median(val[i - 19:i + 1]) >= 5000

        ann_idx = sorted({bisect_left(d, x) for x in alld if bisect_left(d, x) < n})
        ann_set = set(ann_idx)
        reac = {}
        for x in sens:
            t = bisect_left(d, x)
            if t < 1 or t + 1 >= n or c[t - 1] <= 0:
                continue
            reac[t] = c[t + 1] / c[t - 1] - 1
        # Part A
        for t, r in reac.items():
            if not ok(t - 1) or t < 66:
                continue
            base = v[t - 60:t - 10].mean()
            if base <= 0:
                continue
            ratio = v[t - 5:t].sum() / (5 * base)
            pre = c[t - 1] / c[t - 6] - 1 if c[t - 6] > 0 else 0
            g = "坏消息(公告后跌20%+)" if r <= -0.20 else "好消息(公告后涨20%+)" if r >= 0.20 else "其他公告"
            A[g].append((ratio, pre, d[t][:7]))
        # Part B
        bad_days = sorted(t for t, r in reac.items() if r <= -0.20)
        for i in range(61, n - 21):
            if not ok(i) or c[i - 1] <= 0:
                continue
            dr = c[i] / c[i - 1] - 1
            avg = v[i - 20:i].mean()
            spike = avg > 0 and v[i] >= 5 * avg
            quiet = i not in ann_set and (i - 1) not in ann_set
            grp = None
            if spike and dr <= -0.10 and quiet:
                grp = "放量5倍+跌10%+无公告（像CND今天）"
            elif dr <= -0.10 and quiet and avg > 0 and v[i] < 2 * avg:
                grp = "跌10%+但没放量（对照）"
            elif i % 5 == 0:
                grp = "随便一天（对照）"
            if grp is None or o[i + 1] <= 0:
                continue
            rec = {"m": d[i][:7]}
            for h in (10, 20):
                k = bisect_left(bad_days, i + 1)
                rec[f"bad{h}"] = k < len(bad_days) and bad_days[k] <= i + h
                rec[f"ret{h}"] = c[i + h] / o[i + 1] - 1
                rec[f"dd{h}"] = c[i + 1:i + h + 1].min() / o[i + 1] - 1 <= -0.20
            B[grp].append(rec)

    out = {"method": __doc__.strip(), "A": {}, "B": {}}
    for g, L in A.items():
        r = np.array([x[0] for x in L])
        p = np.array([x[1] for x in L])
        out["A"][g] = {"n": len(L), "前5天成交量倍数中位数": round(float(np.median(r)), 2),
                       "前5天放量3倍以上%": round(100 * float((r >= 3).mean()), 1), "前5天放量5倍以上%": round(100 * float((r >= 5).mean()), 1),
                       "前5天已跌10%以上%": round(100 * float((p <= -0.10).mean()), 1), "前5天涨跌中位数%": round(100 * float(np.median(p)), 1)}
    base = B.get("随便一天（对照）", [])
    for g, L in B.items():
        e = {"n": len(L)}
        for h in (10, 20):
            e[f"{h}天内出坏消息%"] = round(100 * float(np.mean([x[f"bad{h}"] for x in L])), 2)
            e[f"{h}天内再跌20%以上%"] = round(100 * float(np.mean([x[f"dd{h}"] for x in L])), 1)
            e[f"{h}天收益中位数%"] = round(100 * float(np.median([x[f"ret{h}"] for x in L])), 1)
            if base and g != "随便一天（对照）":
                bm = defaultdict(list)
                for x in base:
                    bm[x["m"]].append(x[f"bad{h}"])
                diff = [x[f"bad{h}"] - np.mean(bm.get(x["m"], [0])) for x in L]
                e[f"{h}天坏消息比随便一天多的t"] = tstat(diff, [x["m"] for x in L])
        out["B"][g] = e
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("[93] done", flush=True)


if __name__ == "__main__":
    main()
