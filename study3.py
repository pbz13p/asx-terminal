"""Round 3: sectors, stocks and a walk-forward prediction model.

1. Sector map for current listings (ASX company directory, GICS industry group).
2. Per-sector and per-stock behaviour on tradable stocks (price >= $0.20,
   20-day median turnover >= $500k): volatility, measured cost, overnight vs
   intraday drift, short-term reversal.
3. A transparent cross-sectional ridge model on ranked features, trained only
   on past years and tested on the next year (2018 onwards), for 1, 5 and 20
   trading-day horizons. Reports rank IC and a long-only top-decile portfolio
   after measured costs, by year, plus the latest scores.

Writes data/study3_results.json (or STUDY3_OUT).
"""
import bisect
import csv
import io
import json
import math
import os
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

import study
from study2 import cs_spread, median

MIN_PRICE, MIN_VALUE = 0.20, float(os.environ.get("S2_MINV", 5e5))
HORIZONS = (1, 5, 20)
FEATURES = ["r1", "r5", "r20", "r60", "vol20", "vratio", "dist_hi", "overnight", "intraday",
            "sec_r5", "sec_r20", "mkt_r1"]
LABELS = {"r1": "昨天涨跌", "r5": "近 5 天涨跌", "r20": "近 20 天涨跌", "r60": "近 60 天涨跌",
          "vol20": "20 天波动率", "vratio": "成交量放大倍数", "dist_hi": "离 52 周高点",
          "overnight": "昨夜跳空", "intraday": "昨天盘中", "sec_r5": "板块近 5 天",
          "sec_r20": "板块近 20 天", "mkt_r1": "大盘昨天"}
FIRST_TEST_YEAR = int(os.environ.get("S3_FIRST", 2018))


def sector_map():
    urls = ["https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file",
            "https://www.asx.com.au/asx/research/ASXListedCompanies.csv"]
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
            lines = text.splitlines()
            start = next(i for i, l in enumerate(lines) if "ASX code" in l or "ASX Code" in l)
            rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
            out = {}
            for row in rows:
                code = (row.get("ASX code") or row.get("ASX Code") or "").strip()
                ind = (row.get("GICS industry group") or row.get("GICs industry group") or "").strip()
                if code:
                    out[code] = ind or "未分类"
            if out:
                print(f"  sector map: {len(out)} codes from {u}")
                return out
        except Exception as e:
            print(f"  sector map failed from {u}: {type(e).__name__}")
    return {}


ZH = {"Materials": "原材料/矿业", "Energy": "能源", "Banks": "银行", "Financial Services": "金融服务",
      "Insurance": "保险", "Equity Real Estate Investment Trusts (REITs)": "房地产信托",
      "Real Estate Management & Development": "地产开发", "Pharmaceuticals, Biotechnology & Life Sciences": "医药生物",
      "Health Care Equipment & Services": "医疗器械与服务", "Software & Services": "软件",
      "Technology Hardware & Equipment": "科技硬件", "Semiconductors & Semiconductor Equipment": "半导体",
      "Telecommunication Services": "电信", "Media & Entertainment": "媒体娱乐",
      "Consumer Discretionary Distribution & Retail": "零售", "Consumer Services": "消费服务",
      "Consumer Durables & Apparel": "耐用消费品", "Automobiles & Components": "汽车",
      "Food, Beverage & Tobacco": "食品饮料", "Consumer Staples Distribution & Retail": "必需品零售",
      "Household & Personal Products": "个护用品", "Capital Goods": "资本品/工业",
      "Commercial & Professional Services": "商业服务", "Transportation": "交通运输", "Utilities": "公用事业",
      "Not Applic": "未分类", "Class Pend": "未分类", "未分类": "未分类", "其他/已退市": "其他/已退市"}


def zh(sector):
    return ZH.get(sector, sector)


def rank_cs(vals):
    """Cross-sectional rank to [-0.5, 0.5]; ties share the average rank."""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        r = (i + j) / 2 / max(1, n - 1) - .5
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def pearson(x, y):
    n = len(x)
    if n < 5:
        return None
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    return num / den if den else None


def build(uni, sectors):
    """Return per-date cross sections and per-stock / per-sector stats."""
    xjo = study.load("AXJO.INDX")
    mkt = {xjo[k][0]: xjo[k][4] / xjo[k - 1][4] - 1 for k in range(1, len(xjo))}
    per_stock = {}
    raw = defaultdict(list)  # date -> [(code, sector, feats, fwd{h: (gross, cost)})]
    sec_daily = defaultdict(lambda: defaultdict(list))  # sector -> date -> [r1]
    for k, (code, _) in enumerate(uni):
        rows = study.load(f"{code}.AU")
        if len(rows) < 300:
            continue
        sec = sectors.get(code, "其他/已退市")
        n = len(rows)
        vals = [r[6] * r[5] for r in rows]
        sp = [None] + [cs_spread(rows[i - 1][2], rows[i - 1][3], rows[i][2], rows[i][3]) for i in range(1, n)]
        st = {"on": [], "in": [], "r1": [], "cost": [], "big_up_on": [], "big_up_intra": [], "dates": 0}
        hi250 = []
        for i in range(61, n):
            d = rows[i][0]
            if rows[i][5] <= 0 or rows[i - 1][4] <= 0:
                continue
            price, mv = rows[i][6], median(vals[i - 20:i])
            r1 = rows[i][4] / rows[i - 1][4] - 1
            if abs(r1) > 1:
                continue
            if mv > 0:
                sec_daily[sec][d].append(r1)
            if d < "2015-01-01" or price < MIN_PRICE or mv < MIN_VALUE:
                continue
            s = [x for x in sp[i - 19:i + 1] if x is not None]
            cost = study.COMMISSION + max(sum(s) / len(s) if s else .01, (.005 if price < 2 else .01) / price)
            on = rows[i][1] / rows[i - 1][4] - 1
            intra = rows[i][4] / rows[i][1] - 1 if rows[i][1] > 0 else 0
            rets = [rows[j][4] / rows[j - 1][4] - 1 for j in range(i - 19, i + 1)]
            m = sum(rets) / 20
            vol20 = math.sqrt(sum((x - m) ** 2 for x in rets) / 19)
            hi = max(r[2] for r in rows[max(0, i - 250):i + 1])
            f = {"r1": r1, "r5": rows[i][4] / rows[i - 5][4] - 1, "r20": rows[i][4] / rows[i - 20][4] - 1,
                 "r60": rows[i][4] / rows[i - 60][4] - 1, "vol20": vol20,
                 "vratio": rows[i][5] / max(1, median([r[5] for r in rows[i - 20:i]])),
                 "dist_hi": rows[i][4] / hi - 1 if hi > 0 else 0, "overnight": on, "intraday": intra,
                 "mkt_r1": mkt.get(d, 0.0)}
            fwd = {}
            if i + 1 < n and rows[i + 1][5] > 0 and rows[i + 1][1] > 0:
                for h in HORIZONS:
                    if i + h < n:
                        g = rows[i + h][4] / rows[i + 1][1] - 1
                        if abs(g) < 1.5:
                            fwd[h] = (g, cost)
            raw[d].append((code, sec, f, fwd))
            st["dates"] += 1
            st["cost"].append(cost)
            st["r1"].append(r1)
            if i + 1 < n and rows[i + 1][1] > 0 and rows[i + 1][5] > 0:
                on1 = rows[i + 1][1] / rows[i][4] - 1
                in1 = rows[i + 1][4] / rows[i + 1][1] - 1
                if abs(on1) < .5 and abs(in1) < .5:
                    st["on"].append((d, on1))
                    st["in"].append((d, in1))
                    if r1 >= .05:
                        st["big_up_on"].append(on1)
                        st["big_up_intra"].append(in1)
        if st["dates"] >= 250:
            per_stock[code] = (sec, st, rows[-1][0], rows[-1][6])
        if (k + 1) % 300 == 0:
            print(f"\r  round 3 load: {k + 1}/{len(uni)}", end="", flush=True)
    print()
    # sector momentum features (equal-weight sector daily return, all liquid-ish members)
    sec_ret = {s: {d: sum(v) / len(v) for d, v in dd.items() if len(v) >= 3} for s, dd in sec_daily.items()}
    sec_cum = {}
    for s, dd in sec_ret.items():
        ds = sorted(dd)
        cum, c = {}, 1.0
        for d in ds:
            c *= 1 + dd[d]
            cum[d] = c
        sec_cum[s] = (ds, cum)
    for d, items in raw.items():
        for code, sec, f, fwd in items:
            ds, cum = sec_cum.get(sec, ([], {}))
            f["sec_r5"] = f["sec_r20"] = 0.0
            if d in cum:
                j = bisect.bisect_left(ds, d)
                if j >= 20:
                    f["sec_r5"] = cum[d] / cum[ds[j - 5]] - 1
                    f["sec_r20"] = cum[d] / cum[ds[j - 20]] - 1
    return raw, per_stock, sec_ret


def _rank(v):
    import numpy as np
    r = np.empty(len(v))
    r[np.argsort(v, kind="mergesort")] = np.arange(len(v))
    return r / max(1, len(v) - 1) - .5


def model(raw):
    import numpy as np
    dates = [d for d in sorted(raw) if len(raw[d]) >= 30]
    X, Y, C, CODES, SECS = {}, {}, {}, {}, {}
    for d in dates:
        items = raw[d]
        X[d] = np.column_stack([np.ones(len(items))] + [_rank(np.array([it[2][f] for it in items])) for f in FEATURES])
        CODES[d] = [it[0] for it in items]
        SECS[d] = [it[1] for it in items]
        for h in HORIZONS:
            Y[(d, h)] = np.array([it[3][h][0] if h in it[3] else np.nan for it in items])
            C[(d, h)] = np.array([it[3][h][1] if h in it[3] else np.nan for it in items])
    years = sorted({d[:4] for d in dates})
    out = {}
    p = len(FEATURES) + 1
    for h in HORIZONS:
        A = np.zeros((len(dates), p, p))
        B = np.zeros((len(dates), p))
        for k, d in enumerate(dates):
            y = Y[(d, h)]
            m = ~np.isnan(y)
            if m.sum() < 30:
                continue
            x = X[d][m]
            yy = y[m] - y[m].mean()
            A[k] = x.T @ x
            B[k] = x.T @ yy
        cumA, cumB = np.cumsum(A, axis=0), np.cumsum(B, axis=0)

        def weights(cut_idx):
            if cut_idx <= 0:
                return None
            a = cumA[cut_idx - 1].copy()
            lam = 1e-3 * max(1.0, a[0, 0])
            a[1:, 1:] += lam * np.eye(p - 1)
            return np.linalg.solve(a, cumB[cut_idx - 1])

        res = {"by_year": []}
        all_ic, port, bench, prev = [], [], [], set()
        sec_ic = defaultdict(list)
        for yr in years:
            if int(yr) < FIRST_TEST_YEAR:
                continue
            first = next(k for k, d in enumerate(dates) if d[:4] == yr)
            w = weights(first - h)  # embargo: drop the last h days before the test year
            ics, pr, br = [], [], []
            test = [k for k, d in enumerate(dates) if d[:4] == yr]
            for n_, k in enumerate(test):
                d = dates[k]
                y, c = Y[(d, h)], C[(d, h)]
                m = ~np.isnan(y)
                if m.sum() < 30:
                    continue
                sc = X[d] @ w
                ic = np.corrcoef(_rank(sc[m]), _rank(y[m]))[0, 1]
                if not np.isnan(ic):
                    ics.append(float(ic))
                secs = np.array(SECS[d])
                for sname in set(SECS[d]):
                    mm = m & (secs == sname)
                    if mm.sum() >= 8:
                        v = np.corrcoef(_rank(sc[mm]), _rank(y[mm]))[0, 1]
                        if not np.isnan(v):
                            sec_ic[sname].append(float(v))
                if n_ % h == 0:
                    idx = np.where(m)[0]
                    order = idx[np.argsort(-sc[idx])]
                    top = order[:max(5, len(order) // 10)]
                    names = {CODES[d][t] for t in top}
                    turn = len(names - prev) / max(1, len(names))
                    prev = names
                    g = float(y[top].mean())
                    pr.append((g, g - float(c[top].mean()) * turn))
                    br.append(float(y[idx].mean()))
            if ics and pr:
                per = 252 / h
                gm, nm, bm = (sum(q[0] for q in pr) / len(pr), sum(q[1] for q in pr) / len(pr), sum(br) / len(br))
                res["by_year"].append({"year": yr, "ic": sum(ics) / len(ics), "periods": len(pr),
                                       "top_gross": gm * 100, "top_net": nm * 100, "all": bm * 100,
                                       "top_net_ann": ((1 + nm) ** per - 1) * 100, "all_ann": ((1 + bm) ** per - 1) * 100,
                                       "hit": 100 * sum(1 for a, b2 in zip(pr, br) if a[1] > b2) / len(pr)})
                all_ic += ics
                port += pr
                bench += br
        if all_ic:
            arr = np.array(all_ic)
            per = 252 / h
            eq = np.cumprod([1 + q[1] for q in port])
            mdd = float((eq / np.maximum.accumulate(eq) - 1).min())
            yrs = len(port) / per
            beq = float(np.prod([1 + b2 for b2 in bench]))
            res.update({"ic": float(arr.mean()),
                        "ic_t": float(arr.mean() / (arr.std(ddof=1) / math.sqrt(len(arr) / h))),
                        "top_net_cagr": (float(eq[-1]) ** (1 / yrs) - 1) * 100, "all_cagr": (beq ** (1 / yrs) - 1) * 100,
                        "top_gross_avg": 100 * sum(q[0] for q in port) / len(port),
                        "top_net_avg": 100 * sum(q[1] for q in port) / len(port),
                        "all_avg": 100 * sum(bench) / len(bench),
                        "mdd": mdd * 100, "years": yrs})
        w = weights(len(dates) - h)
        res["coef"] = {LABELS[f]: float(w[i + 1]) for i, f in enumerate(FEATURES)}
        d = dates[-1]
        sc = X[d] @ w
        order = np.argsort(-sc)
        items = raw[d]

        def row(t):
            it = items[t]
            return {"code": it[0], "sector": zh(it[1]), "score": float(sc[t]),
                    "r5": it[2]["r5"] * 100, "r20": it[2]["r20"] * 100}
        res["latest_date"] = d
        res["top"] = [row(t) for t in order[:25]]
        res["bottom"] = [row(t) for t in order[-25:][::-1]]
        res["sector_ic"] = {zh(s2): {"ic": sum(v) / len(v), "days": len(v)} for s2, v in sec_ic.items() if len(v) > 100}
        out[str(h)] = res
        print(f"  model h={h}: IC={res.get('ic')}  top net CAGR={res.get('top_net_cagr')}  all CAGR={res.get('all_cagr')}")
    return out


def sector_table(per_stock, sec_ret):
    groups = defaultdict(list)
    for code, (sec, st, last, price) in per_stock.items():
        groups[sec].append((code, st, last, price))
    table = []
    for sec, members in groups.items():
        on = [x for _, st, _, _ in members for _, x in st["on"]]
        intra = [x for _, st, _, _ in members for _, x in st["in"]]
        bu_on = [x for _, st, _, _ in members for x in st["big_up_on"]]
        bu_in = [x for _, st, _, _ in members for x in st["big_up_intra"]]
        r1 = [x for _, st, _, _ in members for x in st["r1"]]
        cost = [x for _, st, _, _ in members for x in st["cost"]]
        if len(on) < 500:
            continue
        m = sum(r1) / len(r1)
        vol = math.sqrt(sum((x - m) ** 2 for x in r1) / (len(r1) - 1)) * math.sqrt(252)
        table.append({"sector": zh(sec), "stocks": len(members), "obs": len(on),
                      "vol": vol * 100, "cost": median(cost) * 100,
                      "overnight": 100 * sum(on) / len(on), "intraday": 100 * sum(intra) / len(intra),
                      "bigup_overnight": 100 * sum(bu_on) / len(bu_on) if bu_on else None,
                      "bigup_intraday": 100 * sum(bu_in) / len(bu_in) if bu_in else None,
                      "bigup_n": len(bu_on)})
    # sector rotation: rank sectors by last 20 trading days, hold 20 days
    rot = []
    common = sorted(set.intersection(*[set(v) for v in sec_ret.values() if len(v) > 1500])) if sec_ret else []
    keep = [s for s, v in sec_ret.items() if len(v) > 1500]
    if common and keep:
        for i in range(40, len(common) - 20, 20):
            if common[i] < "2015-01-01":
                continue
            past = {s: math.prod(1 + sec_ret[s][common[j]] for j in range(i - 19, i + 1)) - 1 for s in keep}
            fut = {s: math.prod(1 + sec_ret[s][common[j]] for j in range(i + 1, i + 21)) - 1 for s in keep}
            order = sorted(keep, key=lambda s: -past[s])
            k = max(1, len(order) // 4)
            rot.append({"d": common[i], "top": sum(fut[s] for s in order[:k]) / k,
                        "bottom": sum(fut[s] for s in order[-k:]) / k, "all": sum(fut.values()) / len(fut)})
    rot_sum = None
    if rot:
        rot_sum = {"periods": len(rot), "top": 100 * sum(r["top"] for r in rot) / len(rot),
                   "bottom": 100 * sum(r["bottom"] for r in rot) / len(rot),
                   "all": 100 * sum(r["all"] for r in rot) / len(rot),
                   "top_beats": 100 * sum(1 for r in rot if r["top"] > r["all"]) / len(rot),
                   "in_top": 100 * sum(r["top"] - r["all"] for r in rot if r["d"] < study.SPLIT) / max(1, sum(1 for r in rot if r["d"] < study.SPLIT)),
                   "out_top": 100 * sum(r["top"] - r["all"] for r in rot if r["d"] >= study.SPLIT) / max(1, sum(1 for r in rot if r["d"] >= study.SPLIT))}
    # recent sector momentum (last 5 / 20 / 60 trading days)
    recent = []
    for s in keep:
        ds = sorted(sec_ret[s])
        if len(ds) < 61:
            continue
        def cum(n):
            return 100 * (math.prod(1 + sec_ret[s][d] for d in ds[-n:]) - 1)
        recent.append({"sector": zh(s), "r5": cum(5), "r20": cum(20), "r60": cum(60), "last": ds[-1]})
    return sorted(table, key=lambda r: -r["stocks"]), rot_sum, sorted(recent, key=lambda r: -r["r20"])


def stock_table(per_stock):
    """Stocks whose overnight-up / intraday-down pattern held in both periods."""
    out = []
    for code, (sec, st, last, price) in per_stock.items():
        on_a = [x for d, x in st["on"] if d < study.SPLIT]
        on_b = [x for d, x in st["on"] if d >= study.SPLIT]
        if len(on_a) < 250 or len(on_b) < 250 or last < "2026-01-01":
            continue

        def mt(a):
            m = sum(a) / len(a)
            sd = math.sqrt(sum((x - m) ** 2 for x in a) / (len(a) - 1))
            return m * 100, (m / (sd / math.sqrt(len(a))) if sd else 0)
        ma, ta = mt(on_a)
        mb, tb = mt(on_b)
        out.append({"code": code, "sector": zh(sec), "price": price, "cost": median(st["cost"]) * 100,
                    "on_in": ma, "t_in": ta, "on_out": mb, "t_out": tb, "days": len(on_a) + len(on_b)})
    out.sort(key=lambda r: -(min(r["t_in"], r["t_out"])))
    return out


def main():
    t0 = time.time()
    sectors = sector_map()
    uni = study.universe()
    study.download([f"{c}.AU" for c, _ in uni] + ["AXJO.INDX"])
    raw, per_stock, sec_ret = build(uni, sectors)
    print(f"  cross-sections: {len(raw)} days, stocks: {len(per_stock)}")
    sec_tab, rot, recent = sector_table(per_stock, sec_ret)
    stocks = stock_table(per_stock)
    mdl = model(raw)
    out = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M"), "minutes": round((time.time() - t0) / 60, 1),
                    "sector_source": bool(sectors), "stocks": len(per_stock), "first_test_year": FIRST_TEST_YEAR,
                    "features": [LABELS[f] for f in FEATURES]},
           "sectors": sec_tab, "rotation": rot, "recent": recent,
           "overnight_stocks": stocks[:40], "overnight_count": len(stocks),
           "overnight_both_t2": sum(1 for s in stocks if s["t_in"] >= 2 and s["t_out"] >= 2),
           "model": mdl}
    p = Path(os.environ.get("STUDY3_OUT", study.DATA / "study3_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"round 3 done in {out['meta']['minutes']} min -> {p}")


if __name__ == "__main__":
    main()
