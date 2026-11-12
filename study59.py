"""Round 59: new listings (IPOs) on the ASX, 2015-2026.

A stock's first row in the EODHD history (incl. delisted stocks) after 2015-01-15 is treated as its listing
day. Measures from the FIRST CLOSE (an ordinary investor can't get IPO allocations): return over 20 / 60 /
250 sessions vs the ASX 200 accumulation index, by first-day pop and by listing size (turnover), plus
the share that ends up delisted within 2 years.
"""
import json
import os
from collections import defaultdict
from pathlib import Path

import study


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    adays = sorted(acc)
    last_day = adays[-1]
    agg = defaultdict(lambda: defaultdict(list))
    n_all = 0
    delisted_2y = 0
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 25:
            continue
        d0 = rows[0][0]
        if d0 < "2015-01-15" or d0 > "2025-10-01" or d0 not in acc:
            continue
        o, c, raw = rows[0][1], rows[0][4], rows[0][6]
        if raw < .05 or o <= 0:
            continue
        n_all += 1
        if rows[-1][0] < last_day and len(rows) < 500:
            delisted_2y += 1
        pop = c / o - 1  # first-day open to close (offer price unknown)
        turn = sorted(r[6] * r[5] for r in rows[:10])[5]
        size = "大(首10日成交中位≥200万)" if turn >= 2e6 else "中(20–200万)" if turn >= 2e5 else "小(<20万)"
        popb = "首日收盘高于开盘" if pop > .02 else "首日收盘低于开盘" if pop < -.02 else "首日平"
        per = "2015–21" if d0 < "2022" else "2022后"
        for h in (20, 60, 250):
            if len(rows) > h:
                e = rows[h][0]
                if e not in acc:
                    continue
                ex = rows[h][4] / c - 1 - (acc[e] / acc[d0] - 1)
            else:
                # delisted before h sessions: use last price vs index to that date
                e = rows[-1][0]
                if e not in acc or rows[-1][0] >= last_day:
                    continue
                ex = rows[-1][4] / c - 1 - (acc[e] / acc[d0] - 1)
            for k in ("全部", size, popb, per):
                agg[k][h].append(ex)
    out = {"listings": n_all, "delisted_within_~2y_share": round(100 * delisted_2y / max(1, n_all), 1)}
    for k, hs in agg.items():
        out[k] = {str(h): {"n": len(v), "mean": round(100 * sum(v) / len(v), 2), "median": round(100 * sorted(v)[len(v) // 2], 2),
                           "beat_index": round(100 * sum(1 for x in v if x > 0) / len(v))} for h, v in hs.items()}
    p = Path(os.environ.get("STUDY59_OUT", study.DATA / "study59_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
