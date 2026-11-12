"""Round 9: which sectors to hold in which macro state, and Australian seasonality.

A. Sector sensitivity: daily beta of each ASX sector to AU 2y yield changes, US 10y changes and AUD;
   forward 20-day sector excess returns (vs ASX 200) by macro state (AU 2y 3-month trend, VIX level).
B. Seasonality: ASX 200 by calendar month and the Christmas window; June tax-loss selling of the
   year's biggest losers and the July rebound (stock level, tradable universe).
"""
import bisect
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study
import study7

SECTORS = {"金融": "AXFJ.INDX", "矿业": "AXMJ.INDX", "科技": "AXIJ.INDX", "能源": "AXEJ.INDX",
           "医疗": "AXHJ.INDX", "地产信托": "AXPJ.INDX", "可选消费": "AXDJ.INDX", "必需消费": "AXSJ.INDX",
           "工业": "AXNJ.INDX", "公用事业": "AXUJ.INDX", "电信": "AXTJ.INDX", "小盘": "AXSO.INDX"}
LOSER_COST = 0.01  # round-trip cost assumption for beaten-down stocks


def closes(sym):
    return {r[0]: r[4] for r in study.load(sym)}


def beta(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx if vx else 0


def mean(a):
    return sum(a) / len(a) if a else None


def part_a():
    study.download(["AXJO.INDX", "VIX.INDX", "AUDUSD.FOREX"] + list(SECTORS.values()))
    xj = closes("AXJO.INDX")
    sec = {k: closes(v) for k, v in SECTORS.items()}
    sec = {k: v for k, v in sec.items() if len(v) > 1000}
    vix, aud = closes("VIX.INDX"), closes("AUDUSD.FOREX")
    f2 = study7.rba_table("f2")
    au2 = f2["FCMYGBAG2D"]
    us10 = study7.fred("DGS10")
    days = [d for d in sorted(xj) if d >= "2015-01-01"]
    k_au2, k_us, k_v, k_aud = sorted(au2), sorted(us10), sorted(vix), sorted(aud)

    def prev(s, ks, d, back=0, strict=True):
        j = (bisect.bisect_left(ks, d) if strict else bisect.bisect_right(ks, d)) - 1 - back
        return s[ks[j]] if j >= 0 else None
    # daily betas (in % per 10bp yield move, % per 1% AUD move), excess vs ASX 200
    betas = {}
    for k, s in sec.items():
        rows = []
        for i in range(1, len(days)):
            d, p = days[i], days[i - 1]
            if d not in s or p not in s or d not in au2 or p not in au2:
                continue
            u_now, u_prev = prev(us10, k_us, d), prev(us10, k_us, p)
            a_now, a_prev = aud.get(d), aud.get(p)
            if None in (u_now, u_prev, a_now, a_prev):
                continue
            rows.append((s[d] / s[p] - xj[d] / xj[p], (au2[d] - au2[p]) * 10, (u_now - u_prev) * 10, a_now / a_prev - 1))
        betas[k] = {"au2": 100 * beta([r[1] for r in rows], [r[0] for r in rows]),
                    "us10": 100 * beta([r[2] for r in rows], [r[0] for r in rows]),
                    "aud": beta([100 * r[3] for r in rows], [100 * r[0] for r in rows]), "n": len(rows)}
    # forward 20-day excess by macro state
    states = defaultdict(lambda: defaultdict(lambda: {"all": [], "in": [], "out": []}))
    for i in range(70, len(days) - 20, 5):
        d = days[i]
        a_now, a_back = prev(au2, k_au2, d, strict=False), prev(au2, k_au2, d, 63, strict=False)
        v = prev(vix, k_v, d)
        tags = []
        if a_now is not None and a_back is not None:
            c = a_now - a_back
            tags.append("市场押降息(澳2年3个月跌>0.5%)" if c < -.5 else "市场押加息(澳2年3个月涨>0.5%)" if c > .5 else "利率预期平稳")
        if v:
            tags.append("VIX≥25 恐慌" if v >= 25 else "VIX<15 平静" if v < 15 else "VIX 15-25 正常")
        mret = xj[days[i + 20]] / xj[d] - 1
        for k, s in sec.items():
            if d in s and days[i + 20] in s:
                ex = s[days[i + 20]] / s[d] - 1 - mret
                for t in tags:
                    st = states[t][k]
                    st["all"].append(ex)
                    st["in" if d < study.SPLIT else "out"].append(ex)
    table = {t: {k: {"n": len(v["all"]), "ex": 100 * mean(v["all"]), "in": 100 * mean(v["in"]) if v["in"] else None,
                     "out": 100 * mean(v["out"]) if v["out"] else None} for k, v in ks.items()} for t, ks in states.items()}
    a_now = au2[k_au2[-1]] - au2[k_au2[-64]]
    return {"betas": betas, "states": table, "now": {"au2_3m_change": a_now, "vix": vix[k_v[-1]], "asof": k_au2[-1]}}


def part_b():
    xj = closes("AXJO.INDX")
    days = sorted(xj)
    by_month = defaultdict(lambda: {"in": [], "out": []})
    month_end = {}
    for d in days:
        month_end[d[:7]] = d
    keys = sorted(month_end)
    for a, b in zip(keys, keys[1:]):
        if a < "2014-12":
            continue
        r = xj[month_end[b]] / xj[month_end[a]] - 1
        by_month[int(b[5:])]["in" if b < "2022" else "out"].append(r)
    months = {m: {"mean": 100 * mean(v["in"] + v["out"]), "up": 100 * sum(1 for x in v["in"] + v["out"] if x > 0) / len(v["in"] + v["out"]),
                  "in": 100 * mean(v["in"]) if v["in"] else None, "out": 100 * mean(v["out"]) if v["out"] else None,
                  "n": len(v["in"] + v["out"])} for m, v in sorted(by_month.items())}
    # Christmas window: close ~Dec 15 to first ~5 trading days of January
    xmas = []
    for y in range(2014, 2026):
        i = bisect.bisect_right(days, f"{y}-12-15") - 1
        j = bisect.bisect_left(days, f"{y + 1}-01-01") + 4
        if 0 <= i < j < len(days):
            xmas.append({"year": y, "r": 100 * (xj[days[j]] / xj[days[i]] - 1)})
    # June tax-loss selling -> July rebound
    stocks = {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) > 300:
            stocks[f.name[:-8]] = rows
    tl = []
    for y in range(2015, 2026):
        picks, allx = [], []
        for code, rows in stocks.items():
            ds = [r[0] for r in rows]
            i0 = bisect.bisect_right(ds, f"{y - 1}-06-30") - 1
            i1 = bisect.bisect_right(ds, f"{y}-06-15") - 1
            i2 = bisect.bisect_right(ds, f"{y}-06-30") - 1
            i3 = bisect.bisect_right(ds, f"{y}-07-31") - 1
            if min(i0, i1, i2, i3) < 21 or not (i0 < i1 < i2 < i3) or ds[i3] < f"{y}-07-20":
                continue
            raw, vol = rows[i1][6], rows[i1][5]
            turn = sorted(rows[k][6] * rows[k][5] for k in range(i1 - 20, i1))[10]
            if raw < .2 or turn < 5e5:
                continue
            ytd = rows[i1][4] / rows[i0][4] - 1
            june = rows[i2][4] / rows[i1][4] - 1
            july = rows[i3][4] / rows[i2][4] - 1
            allx.append(july)
            picks.append((ytd, june, july))
        if len(picks) < 50:
            continue
        picks.sort()
        n = max(10, len(picks) // 10)
        losers = picks[:n]
        med = sorted(allx)[len(allx) // 2]
        tl.append({"year": y, "n": n, "universe": len(picks), "loser_ytd": 100 * mean([p[0] for p in losers]),
                   "loser_late_june": 100 * mean([p[1] for p in losers]), "all_late_june": 100 * mean([p[1] for p in picks]),
                   "loser_july": 100 * mean([p[2] for p in losers]), "all_july": 100 * mean(allx),
                   "excess_july": 100 * (mean([p[2] for p in losers]) - mean(allx)),
                   "net_excess": 100 * (mean([p[2] for p in losers]) - mean(allx) - LOSER_COST),
                   "loser_win": 100 * sum(1 for p in losers if p[2] > med) / n})
    return {"months": months, "xmas": xmas, "taxloss": tl}


def main():
    out = {"sectors": part_a(), "season": part_b()}
    p = Path(os.environ.get("STUDY9_OUT", study.DATA / "study9_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("round 9 done")


if __name__ == "__main__":
    main()
