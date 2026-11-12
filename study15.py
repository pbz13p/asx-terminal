"""Round 15: dividends and franking around ex-dividend dates (large, liquid ASX stocks).

For the ~150 most traded stocks: EODHD dividend history (ex-dates, cash amount). Measure
  - run-up: total return ex-20 -> ex-1 (last cum-dividend close) minus ASX 200 accumulation index
  - ex-day drop ratio: (cum close - ex-day open) / cash dividend, raw prices. With fully franked dividends
    the value to an Australian holder is up to 1.43x the cash; a ratio < 1 means the price fell less
    than the cash paid.
  - after: total return ex-day open -> ex+20 minus the accumulation index
Split by period. Grouped by franking level from the feed.
"""
import bisect
import json
import os
from collections import defaultdict
from pathlib import Path

import study

N_STOCKS = 150


def main():
    study.download(["AXJOA.INDX"])
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    # pick the most traded stocks over the last year in the cache
    cands = []
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 1500 or rows[-1][0] < "2026-10-01":
            continue
        t = sorted(r[6] * r[5] for r in rows[-250:])
        cands.append((t[len(t) // 2], f.name[:-8]))
    cands.sort(reverse=True)
    codes = [c for _, c in cands[:N_STOCKS]]
    res = defaultdict(lambda: defaultdict(list))
    per_stock = {}
    for code in codes:
        divs = study.api(f"div/{code}.AU", **{"from": "2014-06-01"})
        if not isinstance(divs, list):
            continue
        rows = study.load(f"{code}.AU")
        ds = [r[0] for r in rows]
        ratios = []
        for dv in divs:
            ex = dv.get("date")
            cash = dv.get("unadjustedValue") or dv.get("value")
            if not ex or not cash or ex < "2015-01-01":
                continue
            i = bisect.bisect_left(ds, ex)
            if i >= len(ds) or ds[i] != ex or i < 21 or i + 20 >= len(ds):
                continue
            _, o, h, l, a, v, raw = rows[i]
            k_ex = raw / a  # adjusted -> raw factor on ex day
            raw_open = o * k_ex
            cum_close = rows[i - 1][6]
            if cum_close <= 0 or cash / cum_close > .15:
                continue
            yld = cash / cum_close
            drop = (cum_close - raw_open) / cash
            per = "in" if ex < study.SPLIT else "out"
            try:
                fr = float(str(dv.get("franking") or "").strip("% ") or "nan")
            except ValueError:
                fr = float("nan")
            fb = "全额franking" if fr >= 99 else "无franking" if fr <= 1 else "部分franking" if fr == fr else "franking未知"
            ex_d, pre_d, post_d = ds[i - 1], ds[i - 20], ds[i + 20]
            if pre_d in acc and ex_d in acc and post_d in acc:
                run = rows[i - 1][4] / rows[i - 20][4] - 1 - (acc[ex_d] / acc[pre_d] - 1)
                after = rows[i + 20][4] / o - 1 - (acc[post_d] / acc[ex_d] - 1)
                for p in ("all", per, fb):
                    res[p]["run"].append(run)
                    res[p]["after"].append(after)
            for p in ("all", per, fb, fb + "|" + per):
                res[p]["drop"].append(drop)
                res[p]["yield"].append(yld)
            ratios.append(drop)
        if ratios:
            per_stock[code] = {"n": len(ratios), "drop_median": sorted(ratios)[len(ratios) // 2]}
    med = lambda a: sorted(a)[len(a) // 2] if a else None
    mean = lambda a: sum(a) / len(a) if a else None
    out = {"stocks": len(per_stock), "groups": {}}
    for p, d in res.items():
        out["groups"][p] = {"n": len(d["drop"]), "drop_median": med(d["drop"]), "drop_mean": mean(d["drop"]),
                            "yield_median": 100 * med(d["yield"]),
                            "run_mean": 100 * mean(d["run"]) if d["run"] else None,
                            "run_up_share": 100 * sum(1 for x in d["run"] if x > 0) / len(d["run"]) if d["run"] else None,
                            "after_mean": 100 * mean(d["after"]) if d["after"] else None}
    out["lowest_drop"] = sorted(({"code": k, **v} for k, v in per_stock.items() if v["n"] >= 8), key=lambda x: x["drop_median"])[:15]
    out["highest_drop"] = sorted(({"code": k, **v} for k, v in per_stock.items() if v["n"] >= 8), key=lambda x: -x["drop_median"])[:15]
    p = Path(os.environ.get("STUDY15_OUT", study.DATA / "study15_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
