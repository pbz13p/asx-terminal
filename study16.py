"""Round 16: is the pre-dividend run-up tradeable, or is it just the results announcement?

Companies announce the dividend with their results, usually 1-4 weeks before the ex-date, so the
20-day window before ex-date often contains the results day. A trader only knows the ex-date after
that announcement. So split the window: far (ex-20 .. ex-11), near (ex-10 .. ex-6), last week
(ex-5 .. ex-1). Only the windows after the announcement are tradeable.

Universe: every cached stock (incl. delisted) whose 20-day median turnover was >= $2M just before entry.
Entry at the open of the first day of the window, exit at the close of the last cum-dividend day.
Excess = stock total return - ASX 200 accumulation index. Cost = Corwin-Schultz spread + 0.1%.
t-stats cluster by ex-date.
"""
import bisect
import json
import math
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import study
import study2

WINDOWS = {"远(前20–11天)": (20, 11), "中(前10–6天)": (10, 6), "最后一周(前5–1天)": (5, 1), "前10–1天": (10, 1), "前20–1天": (20, 1)}


def tstat(by_date):
    xs = [sum(v) / len(v) for v in by_date.values()]
    n = len(xs)
    if n < 5:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd else None


def main():
    study.download(["AXJOA.INDX"])
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    adays = sorted(acc)
    stocks = {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 300:
            continue
        t = sorted(r[6] * r[5] for r in rows)
        if t[int(len(t) * .8)] >= 2e6:  # was liquid at least a fifth of the time
            stocks[f.name[:-8]] = rows
    print("  candidate stocks:", len(stocks))

    def fetch(code):
        try:
            d = study.api(f"div/{code}.AU", **{"from": "2014-06-01"})
            return code, d if isinstance(d, list) else []
        except Exception:
            return code, []
    with ThreadPoolExecutor(max_workers=6) as ex:
        divs = dict(ex.map(fetch, list(stocks)))
    agg = defaultdict(lambda: {"ex": [], "net": [], "by_date": defaultdict(list), "year": defaultdict(list)})
    n_events = 0
    for code, rows in stocks.items():
        ds = [r[0] for r in rows]
        for dv in divs.get(code, []):
            exd = dv.get("date")
            cash = dv.get("unadjustedValue") or dv.get("value")
            if not exd or not cash or exd < "2015-01-01":
                continue
            i = bisect.bisect_left(ds, exd)
            if i >= len(ds) or ds[i] != exd or i < 45:
                continue
            cum = rows[i - 1][6]
            if cum <= 0 or cash / cum > .15 or cash / cum < .002:
                continue
            turn = sorted(rows[k][6] * rows[k][5] for k in range(i - 41, i - 21))[10]
            if turn < 2e6 or rows[i - 21][6] < .5:
                continue
            n_events += 1
            yld = cash / cum
            yb = "股息率<1%" if yld < .01 else "股息率1–2.5%" if yld < .025 else "股息率>2.5%"
            try:
                fr = float(str(dv.get("franking") or "").strip("% ") or "nan")
            except ValueError:
                fr = float("nan")
            fb = "全额franking" if fr >= 99 else "无franking" if fr <= 1 else "部分franking"
            per = "2015–21" if exd < study.SPLIT else "2022后"
            sp = [study2.cs_spread(rows[k - 1][2], rows[k - 1][3], rows[k][2], rows[k][3]) for k in range(i - 40, i - 20)]
            cost = study2.median([x for x in sp if x is not None]) + study.COMMISSION
            for w, (a, b) in WINDOWS.items():
                e_i, x_i = i - a, i - b
                entry, exit_ = rows[e_i][1], rows[x_i][4]
                d0, d1 = ds[e_i - 1], ds[x_i]
                if entry <= 0 or d0 not in acc or d1 not in acc:
                    continue
                # index measured from the prior close to the exit close (entry is that morning's open)
                r = exit_ / entry - 1
                m = acc[d1] / acc[d0] - 1
                for key in (w, f"{w}|{per}", f"{w}|{yb}", f"{w}|{fb}"):
                    g = agg[key]
                    g["ex"].append(r - m)
                    g["net"].append(r - m - cost)
                    g["by_date"][exd].append(r - m)
                    if key == w:
                        g["year"][exd[:4]].append(r - m)
    out = {"events": n_events, "stocks": len(stocks), "groups": {}}
    for k, g in agg.items():
        xs = sorted(g["ex"])
        out["groups"][k] = {"n": len(xs), "mean": 100 * sum(xs) / len(xs), "median": 100 * xs[len(xs) // 2],
                            "net": 100 * sum(g["net"]) / len(g["net"]), "win": 100 * sum(1 for x in xs if x > 0) / len(xs),
                            "t": tstat(g["by_date"]),
                            "years": {y: round(100 * sum(v) / len(v), 2) for y, v in sorted(g["year"].items())} if g["year"] else None}
    p = Path(os.environ.get("STUDY16_OUT", study.DATA / "study16_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("round 16 done", n_events)


if __name__ == "__main__":
    main()
