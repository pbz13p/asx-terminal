"""Round 17: commodities -> ASX resource stocks (gold, oil, copper, lithium, uranium).

US-listed commodity ETFs trade while Australia sleeps. For each theme we build an equal-weight basket of
ASX stocks and ask:
 A. Overnight: does yesterday's US move (in AUD) show up in today's ASX open (gap) — and is anything
    left for the rest of the day (open -> close)? If the intraday part is zero, the move is priced at
    the open and chasing it is pointless.
 B. Divergence: when the basket has lagged / run ahead of its commodity over 20 days (beyond its usual
    sensitivity), does it catch up over the next 5 / 20 days?
 C. Commodity trend: does the commodity's own 20-day trend predict the basket's next 20 days?
Both periods reported.
"""
import bisect
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study

THEMES = {
    "黄金": ("GLD.US", ["NST", "EVN", "RRL", "PRU", "RMS", "WGX", "GOR", "DEG", "CMM", "RSG", "SBM", "WAF", "PDI", "BGL", "GMD", "VAU", "SLR"]),
    "石油天然气": ("USO.US", ["WDS", "STO", "BPT", "KAR", "ORG", "STX", "CVN", "SXY"]),
    "铜": ("CPER.US", ["SFR", "29M", "AIS", "HCH", "C6C", "AZY", "S32", "OZL", "SND", "CYM"]),
    "锂": ("LIT.US", ["PLS", "MIN", "IGO", "LTR", "AKE", "CXO", "SYA", "LKE", "VUL", "GL1", "INR", "ORE"]),
    "铀": ("URA.US", ["PDN", "BOE", "DYL", "LOT", "BMN", "PEN", "AGE", "DEV", "NXG"]),
}


def series(sym):
    return {r[0]: r for r in study.load(sym)}


def corr(xs, ys):
    n = len(xs)
    if n < 30:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else None


def beta(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    v = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / v if v else 0


def main():
    syms = ["AUDUSD.FOREX", "AXJOA.INDX"] + [t[0] for t in THEMES.values()]
    for _, (_, members) in THEMES.items():
        syms += [f"{m}.AU" for m in members]
    study.download(sorted(set(syms)))
    aud = {r[0]: r[4] for r in study.load("AUDUSD.FOREX")}
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    adays = sorted(acc)
    out = {}
    for theme, (etf, members) in THEMES.items():
        e = {r[0]: r[4] for r in study.load(etf)}
        ekeys = sorted(e)
        akeys = sorted(aud)
        if len(e) < 500:
            out[theme] = {"error": f"{etf} 数据不足"}
            continue
        mem = {m: series(f"{m}.AU") for m in members}
        mem = {m: v for m, v in mem.items() if len(v) > 300}

        def etf_aud(d):  # last US close strictly before ASX date d, in AUD
            j = bisect.bisect_left(ekeys, d) - 1
            k = bisect.bisect_left(akeys, d) - 1
            if j < 1 or k < 0:
                return None, None
            return e[ekeys[j]] / aud[akeys[k]], ekeys[j]
        rows = []  # date, us_ret(aud), gap, intraday, close-to-close basket, idx
        prev_us = None
        for i, d in enumerate(adays):
            if d < "2015-01-01" or i == 0:
                continue
            p = adays[i - 1]
            us_now, us_day = etf_aud(d)
            us_prev, _ = etf_aud(p)
            if us_now is None or us_prev is None or us_day is None:
                continue
            gaps, intras, ccs = [], [], []
            for m, s in mem.items():
                if d in s and p in s:
                    _, o, h, l, c, v, raw = s[d]
                    pc = s[p][4]
                    if o > 0 and pc > 0 and s[p][6] >= .1 and v > 0:
                        gaps.append(o / pc - 1)
                        intras.append(c / o - 1)
                        ccs.append(c / pc - 1)
            if len(gaps) < 3:
                continue
            mean = lambda a: sum(a) / len(a)
            rows.append((d, us_now / us_prev - 1, mean(gaps), mean(intras), mean(ccs), acc[d] / acc[p] - 1 if d in acc and p in acc else 0))
        res = {"etf": etf, "members_used": len(mem), "days": len(rows)}
        for per, sel in (("all", lambda d: True), ("2015–21", lambda d: d < study.SPLIT), ("2022后", lambda d: d >= study.SPLIT)):
            rr = [r for r in rows if sel(r[0])]
            us, gap, intra = [r[1] for r in rr], [r[2] for r in rr], [r[3] for r in rr]
            big_up = [r for r in rr if r[1] > .02]
            big_dn = [r for r in rr if r[1] < -.02]
            res[per] = {"corr_gap": corr(us, gap), "corr_intraday": corr(us, intra), "beta_gap": beta(us, gap),
                        "us_up2": {"n": len(big_up), "gap": 100 * sum(r[2] for r in big_up) / max(1, len(big_up)),
                                   "intraday": 100 * sum(r[3] for r in big_up) / max(1, len(big_up))},
                        "us_dn2": {"n": len(big_dn), "gap": 100 * sum(r[2] for r in big_dn) / max(1, len(big_dn)),
                                   "intraday": 100 * sum(r[3] for r in big_dn) / max(1, len(big_dn))}}
        # B/C: cumulative indices
        bi, ui = [1.0], [1.0]
        for r in rows:
            bi.append(bi[-1] * (1 + r[4] - r[5]))  # basket excess vs ASX 200 (accumulation)
            ui.append(ui[-1] * (1 + r[1]))
        dates = [r[0] for r in rows]
        bt = beta([r[1] for r in rows], [r[4] - r[5] for r in rows])
        div = defaultdict(lambda: defaultdict(lambda: {"f5": [], "f20": []}))
        trend = defaultdict(lambda: defaultdict(list))
        resid = []
        for i in range(20, len(rows) - 20):
            b20 = bi[i + 1] / bi[i - 19] - 1
            u20 = ui[i + 1] / ui[i - 19] - 1
            resid.append((i, b20 - bt * u20, u20))
        sds = math.sqrt(sum(x[1] ** 2 for x in resid) / len(resid))
        for i, rsd, u20 in resid:
            per = "2015–21" if dates[i] < study.SPLIT else "2022后"
            z = rsd / sds
            g = "股票落后商品(z<-1)" if z < -1 else "股票跑赢商品(z>1)" if z > 1 else "正常"
            f5 = (bi[i + 6] / bi[i + 1] - 1) - bt * (ui[i + 6] / ui[i + 1] - 1)
            f20 = (bi[i + 21] / bi[i + 1] - 1) - bt * (ui[i + 21] / ui[i + 1] - 1)
            for p in ("all", per):
                div[g][p]["f5"].append(f5)
                div[g][p]["f20"].append(f20)
            tg = "商品20天涨>5%" if u20 > .05 else "商品20天跌>5%" if u20 < -.05 else "商品平稳"
            fb = bi[i + 21] / bi[i + 1] - 1  # basket excess vs ASX 200 next 20 days
            for p in ("all", per):
                trend[tg][p].append(fb)
        mean = lambda a: 100 * sum(a) / len(a) if a else None
        res["beta_daily"] = bt
        res["divergence"] = {g: {p: {"n": len(v["f20"]), "f5": mean(v["f5"]), "f20": mean(v["f20"])} for p, v in ps.items()} for g, ps in div.items()}
        res["trend"] = {g: {p: {"n": len(v), "f20": mean(v)} for p, v in ps.items()} for g, ps in trend.items()}
        i = len(rows) - 1
        b20 = bi[i + 1] / bi[i - 19] - 1
        u20 = ui[i + 1] / ui[i - 19] - 1
        res["now"] = {"date": dates[-1], "z": (b20 - bt * u20) / sds, "commodity_20d": 100 * u20, "basket_vs_xjo_20d": 100 * b20}
        out[theme] = res
    p = Path(os.environ.get("STUDY17_OUT", study.DATA / "study17_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
