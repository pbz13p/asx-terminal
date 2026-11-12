"""Round 6.
A. Monthly investing into the ASX 200: plain DCA vs "keep half in reserve, deploy the reserve on panic",
   with several panic thresholds; started 2015 and 2022.
B. Overnight after a big up day, per year and per stock: buy closing auction, sell next opening auction.
"""
import bisect
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study

AUCTION_COST = 0.0016  # commission both sides, no spread in auctions


def closes(sym):
    return {r[0]: r[4] for r in study.load(sym)}


def dca(xj, vix, start, rule, thr):
    days = [d for d in sorted(xj) if d >= start]
    vd = sorted(vix)
    allx = sorted(xj)
    units = cash = paid = 0.0
    last_month, cool = "", 0
    deploys = 0
    for d in days:
        i = allx.index(d) if False else bisect.bisect_left(allx, d)
        px = xj[d]
        j = bisect.bisect_left(vd, d) - 1
        v = vix[vd[j]] if j >= 0 else 0
        hi = max(xj[allx[k]] for k in range(max(0, i - 252), i)) if i else px
        prev = xj[allx[i - 1]] if i else px
        panic = (rule == "vix" and v >= thr) or (rule == "dd" and prev / hi - 1 <= -thr / 100)
        if d[:7] != last_month:
            last_month = d[:7]
            paid += 1000
            if rule == "plain":
                units += 1000 / px
            else:
                units += 500 / px
                cash += 500
        cool = max(0, cool - 1)
        if rule != "plain" and panic and cool == 0 and cash > 0:
            units += cash / px
            cash, cool = 0.0, 20
            deploys += 1
    end = units * xj[days[-1]] + cash
    return {"paid": paid, "end": end, "gain": 100 * (end / paid - 1), "cash_left": cash, "deploys": deploys}


def part_a():
    study.download(["AXJO.INDX", "VIX.INDX"])
    xj, vix = closes("AXJO.INDX"), closes("VIX.INDX")
    out = []
    for start in ("2015-01-01", "2022-01-01"):
        for rule, thr, label in [("plain", 0, "普通定投"), ("vix", 20, "VIX≥20 时投入储备"), ("vix", 25, "VIX≥25 时投入储备"),
                                 ("vix", 30, "VIX≥30 时投入储备"), ("dd", 5, "大盘离一年高点跌 5%"),
                                 ("dd", 10, "大盘离一年高点跌 10%"), ("dd", 15, "大盘离一年高点跌 15%")]:
            r = dca(xj, vix, start, rule, thr)
            r.update(start=start, rule=label)
            out.append(r)
    return out


def part_b():
    by_year = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0.0, 0]))  # year -> bucket -> n, on, intra, wins
    per_stock = defaultdict(lambda: {"in": [], "out": []})
    for f in sorted(study.CACHE.glob("*.AU.json")):
        rows = study.load(f.name[:-5])
        for i in range(21, len(rows) - 1):
            d, o, h, l, c, v, raw = rows[i]
            pc = rows[i - 1][4]
            if raw < .2 or pc <= 0:
                continue
            turn = sorted(rows[k][6] * rows[k][5] for k in range(i - 20, i))[10]
            if turn < 5e5:
                continue
            r = c / pc - 1
            b = "涨10–20%" if .10 <= r < .20 else "涨≥20%" if r >= .20 else "涨5–10%" if r >= .05 else None
            if b is None:
                continue
            no, nc = rows[i + 1][1], rows[i + 1][4]
            on, intra = no / c - 1, nc / no - 1
            if abs(on) > .5:
                continue
            for bb in (b, "全部 ≥5%"):
                a = by_year[d[:4]][bb]
                a[0] += 1; a[1] += on; a[2] += intra; a[3] += on > AUCTION_COST
            per_stock[f.name[:-8]]["in" if d < study.SPLIT else "out"].append(on)
    years = {y: {b: {"n": a[0], "overnight": 100 * a[1] / a[0], "intraday": 100 * a[2] / a[0],
                     "net": 100 * (a[1] / a[0] - AUCTION_COST), "win": 100 * a[3] / a[0]}
                 for b, a in bs.items() if a[0]} for y, bs in sorted(by_year.items())}
    stocks = []
    for code, s in per_stock.items():
        if len(s["in"]) >= 8 and len(s["out"]) >= 5:
            mi, mo = sum(s["in"]) / len(s["in"]), sum(s["out"]) / len(s["out"])
            stocks.append({"code": code, "n_in": len(s["in"]), "n_out": len(s["out"]), "in": 100 * mi, "out": 100 * mo})
    stocks.sort(key=lambda x: -x["out"])
    both = [x for x in stocks if x["in"] > .16 and x["out"] > .16]
    return {"years": years, "stocks_top": stocks[:30], "stocks_n": len(stocks), "both_positive_net": both[:40],
            "both_n": len(both)}


def main():
    out = {"dca": part_a(), "overnight": part_b()}
    p = Path(os.environ.get("STUDY6_OUT", study.DATA / "study6_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("round 6 done")


if __name__ == "__main__":
    main()
