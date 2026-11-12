"""Round 4: macro drivers of ASX swings at day / week / month horizons.

1. Event study: ASX 200 and sector index moves around economic releases
   (AU CPI, jobs, RBA rate decisions, US CPI, FOMC, US payrolls), split by
   surprise (actual vs consensus) where the feed has estimates.
2. Volatility forecasting: next 5 / 20 trading-day realised volatility of the
   ASX 200 from its own past volatility (HAR), US VIX, and known upcoming
   events. Walk-forward (fit on past years, test on the next), compared with a
   naive "same as last month" forecast.
3. Macro regimes: forward 20 / 60-day ASX 200 returns and volatility by
   VIX level, AU 10-year yield trend, AUD trend and gold trend.

Writes data/study4_results.json (or STUDY4_OUT).
"""
import json
import math
import os
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import study

INDEX = {"XJO": "AXJO.INDX", "金融": "AXFJ.INDX", "矿业": "AXMJ.INDX", "科技": "AXIJ.INDX",
         "能源": "AXEJ.INDX", "医疗": "AXHJ.INDX", "地产信托": "AXPJ.INDX", "小盘": "AXSO.INDX"}
MACRO = {"VIX": "VIX.INDX", "AUDUSD": "AUDUSD.FOREX", "GOLD": "XAUUSD.FOREX", "SPX": "GSPC.INDX"}
SPLIT = study.SPLIT


def closes(symbol):
    rows = study.load(symbol)
    return {r[0]: r[4] for r in rows}


# ---------------------------------------------------------------- events
EVENT_RULES = [  # (label, country, words that must appear in the event type, words to skip)
    ("澳洲 CPI", "AU", ("inflation",), ("expectation", "trimmed", "weighted")),
    ("澳洲 就业/失业率", "AU", ("unemployment",), ()),
    ("澳联储 利率决议", "AU", ("interest rate", "cash rate"), ()),
    ("美国 CPI", "US", ("inflation rate",), ("core", "expectation")),
    ("美联储 利率决议", "US", ("fed interest rate", "interest rate decision"), ()),
    ("美国 非农", "US", ("non farm payrolls", "nonfarm payrolls"), ("private", "adp")),
]


def fetch_events():
    out = []
    for country in ("AU", "US"):
        start = date(2014, 6, 1)
        while start < date.today() + timedelta(days=60):
            end = min(start + timedelta(days=180), date.today() + timedelta(days=60))
            offset = 0
            while True:
                rows = study.api("economic-events", country=country, **{"from": start.isoformat(), "to": end.isoformat()},
                                 limit=1000, offset=offset)
                if not isinstance(rows, list) or not rows:
                    break
                out += rows
                if len(rows) < 1000:
                    break
                offset += 1000
            start = end + timedelta(days=1)
    return out


def classify(events):
    picked = defaultdict(dict)  # label -> date -> event
    for e in events:
        t = (e.get("type") or "").lower()
        c = (e.get("country") or "").upper()
        d = (e.get("date") or "")[:10]
        if not d:
            continue
        for label, cc, must, skip in EVENT_RULES:
            if c == cc and any(w in t for w in must) and not any(w in t for w in skip):
                cmp = (e.get("comparison") or "").lower()
                if label.endswith("CPI") and cmp and cmp not in ("yoy", "y/y", "qoq", "q/q", "mom", "m/m"):
                    continue
                prev = picked[label].get(d)
                if prev is None or (prev.get("estimate") is None and e.get("estimate") is not None):
                    picked[label][d] = e
    return picked


FOMC = """2015-01-28 2015-03-18 2015-04-29 2015-06-17 2015-07-29 2015-09-17 2015-10-28 2015-12-16+
2016-01-27 2016-03-16 2016-04-27 2016-06-15 2016-07-27 2016-09-21 2016-11-02 2016-12-14+
2017-02-01 2017-03-15+ 2017-05-03 2017-06-14+ 2017-07-26 2017-09-20 2017-11-01 2017-12-13+
2018-01-31 2018-03-21+ 2018-05-02 2018-06-13+ 2018-08-01 2018-09-26+ 2018-11-08 2018-12-19+
2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31- 2019-09-18- 2019-10-30- 2019-12-11
2020-01-29 2020-03-03- 2020-03-15- 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16+ 2022-05-04+ 2022-06-15+ 2022-07-27+ 2022-09-21+ 2022-11-02+ 2022-12-14+
2023-02-01+ 2023-03-22+ 2023-05-03+ 2023-06-14 2023-07-26+ 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18- 2024-11-07- 2024-12-18-
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17- 2025-10-29- 2025-12-10-
2026-01-28? 2026-03-18? 2026-04-29? 2026-06-17? 2026-07-29? 2026-09-16? 2026-10-28? 2026-12-09?"""
RBA_CHANGES = {"2015-02-03": "-", "2015-05-05": "-", "2016-05-03": "-", "2016-08-02": "-", "2019-06-04": "-",
               "2019-07-02": "-", "2019-10-01": "-", "2020-03-03": "-", "2020-03-19": "-", "2020-11-03": "-",
               "2022-05-03": "+", "2022-06-07": "+", "2022-07-05": "+", "2022-08-02": "+", "2022-09-06": "+",
               "2022-10-04": "+", "2022-11-01": "+", "2022-12-06": "+", "2023-02-07": "+", "2023-03-07": "+",
               "2023-05-02": "+", "2023-06-06": "+", "2023-11-07": "+", "2025-02-18": "-", "2025-05-20": "-",
               "2025-08-12": "-"}
RBA_NEW = ("2024-02-06 2024-03-19 2024-05-07 2024-06-18 2024-08-06 2024-09-24 2024-11-05 2024-12-10 "
           "2025-02-18 2025-04-01 2025-05-20 2025-07-08 2025-08-12 2025-09-30 2025-11-04 2025-12-09 "
           "2026-02-03? 2026-03-17? 2026-05-05? 2026-06-16? 2026-08-11? 2026-09-29? 2026-11-03? 2026-12-08?")


def first_weekday(y, m, wd):
    d = date(y, m, 1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d


def fallback_events():
    """Curated calendar: FOMC and RBA decisions with outcome, AU quarterly CPI and US payrolls by rule.
    The EODHD plan has no economic-events feed, so consensus surprises are not available; for central
    banks the 'surprise' split is rate changed (+/-) vs hold."""
    picked = defaultdict(dict)
    for tok in FOMC.split():
        d, flag = tok[:10], tok[10:]
        picked["美联储 利率决议"][d] = {"move": None if flag == "?" else flag or "="}
    rba = []
    for y in range(2015, 2024):
        rba += [first_weekday(y, m, 1).isoformat() for m in range(2, 13)]
    rba.append("2020-03-19")
    for tok in RBA_NEW.split():
        rba.append(tok)
    for tok in rba:
        d = tok[:10]
        picked["澳联储 利率决议"][d] = {"move": None if tok.endswith("?") else RBA_CHANGES.get(d, "=")}
    for y in range(2015, date.today().year + 1):
        for m in (1, 4, 7, 10):  # AU quarterly CPI: last Wednesday of the month after the quarter end
            d = (date(y, m + 1, 1) - timedelta(days=1))
            while d.weekday() != 2:
                d -= timedelta(days=1)
            picked["澳洲 季度CPI"][d.isoformat()] = {}
        for m in range(1, 13):  # US payrolls: first Friday (BLS convention; a few holiday exceptions)
            picked["美国 非农就业"][first_weekday(y, m, 4).isoformat()] = {}
    # AU monthly jobs report: usually the Thursday in the middle of the month (approximate)
    for y in range(2015, date.today().year + 1):
        for m in range(1, 13):
            picked["澳洲 就业数据(约)"][(first_weekday(y, m, 3) + timedelta(days=14)).isoformat()] = {}
    return picked


def asx_day_for(event_date, country, trading_days):
    """First ASX session that can react: same day for AU, next session for US (released overnight Sydney)."""
    import bisect
    i = bisect.bisect_left(trading_days, event_date)
    if country == "US":
        i = bisect.bisect_right(trading_days, event_date)
    return trading_days[i] if i < len(trading_days) else None


def event_study(picked, series):
    days = sorted(series["XJO"])
    idx = {d: i for i, d in enumerate(days)}
    xj = series["XJO"]
    rets = {days[i]: xj[days[i]] / xj[days[i - 1]] - 1 for i in range(1, len(days))}
    absr = [abs(v) for v in rets.values()]
    base_abs = sum(absr) / len(absr)
    rows, upcoming = [], []
    for label, evs in picked.items():
        country = "US" if label.startswith("美") else "AU"
        hits = []
        for d, e in sorted(evs.items()):
            if d > date.today().isoformat():
                if d <= (date.today() + timedelta(days=45)).isoformat():
                    upcoming.append({"event": label, "date": d, "asx_day": "当天" if country == "AU" else "下一个交易日"})
                continue
            if d < "2015-01-01":
                continue
            ad = asx_day_for(d, country, days)
            if ad is None or ad not in idx or idx[ad] + 20 >= len(days) or idx[ad] < 5:
                continue
            i = idx[ad]
            act, est = e.get("actual"), e.get("estimate")
            surprise = None
            if isinstance(act, (int, float)) and isinstance(est, (int, float)):
                surprise = act - est
            elif e.get("move"):
                surprise = {"+": 1, "-": -1, "=": 0}[e["move"]]
            r0 = rets[ad]
            r5 = xj[days[i + 5]] / xj[days[i - 1]] - 1
            r20 = xj[days[i + 20]] / xj[days[i - 1]] - 1
            sec = {k: series[k][days[i]] / series[k][days[i - 1]] - 1
                   for k in series if k != "XJO" and days[i] in series[k] and days[i - 1] in series[k]}
            hits.append({"d": ad, "r0": r0, "r5": r5, "r20": r20, "abs": abs(r0), "s": surprise, "sec": sec})
        if len(hits) < 8:
            continue

        def summ(hs):
            if not hs:
                return None
            n = len(hs)
            return {"n": n, "abs": 100 * sum(h["abs"] for h in hs) / n, "r0": 100 * sum(h["r0"] for h in hs) / n,
                    "r5": 100 * sum(h["r5"] for h in hs) / n, "r20": 100 * sum(h["r20"] for h in hs) / n,
                    "up": 100 * sum(1 for h in hs if h["r0"] > 0) / n,
                    "sec": {k: 100 * sum(h["sec"].get(k, 0) for h in hs) / n for k in INDEX if k != "XJO"}}
        with_s = [h for h in hits if h["s"] is not None]
        hi = [h for h in with_s if h["s"] > 0]
        lo = [h for h in with_s if h["s"] < 0]
        eq = [h for h in with_s if h["s"] == 0]
        rows.append({"event": label, "all": summ(hits), "vol_ratio": (sum(h["abs"] for h in hits) / len(hits)) / base_abs,
                     "above": summ(hi), "below": summ(lo), "inline": summ(eq),
                     "in": summ([h for h in hits if h["d"] < SPLIT]), "out": summ([h for h in hits if h["d"] >= SPLIT]),
                     "biggest": sorted(({"d": h["d"], "r0": 100 * h["r0"], "s": h["s"]} for h in hits),
                                       key=lambda h: -abs(h["r0"]))[:5]})
    return rows, sorted(upcoming, key=lambda u: u["date"])[:30], 100 * base_abs


# ---------------------------------------------------------------- vol forecast
def realised(rets, i, n):
    w = rets[i - n + 1:i + 1]
    return math.sqrt(sum(x * x for x in w) / len(w) * 252)


def ols(X, y):
    import numpy as np
    X, y = np.array(X), np.array(y)
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return b


def vol_forecast(series, picked):
    import numpy as np
    days = sorted(series["XJO"])
    xj = series["XJO"]
    rets = [0.0] + [math.log(xj[days[i]] / xj[days[i - 1]]) for i in range(1, len(days))]
    vix = series.get("VIX", {})
    vix_days = sorted(vix)
    import bisect

    def vix_on(d):
        j = bisect.bisect_left(vix_days, d)  # last US close strictly before this ASX day
        return vix[vix_days[j - 1]] if j > 0 else None
    ev_days = set()
    for label, evs in picked.items():
        country = "US" if label.startswith("美") else "AU"
        for d in evs:
            ad = asx_day_for(d, country, days)
            if ad:
                ev_days.add(ad)
    out = {}
    for h in (5, 20):
        rows = []
        for i in range(70, len(days) - h):
            if days[i] < "2015-01-01":
                continue
            v = vix_on(days[i + 1]) if vix_days else None
            fut = realised(rets, i + h, h)
            n_ev = sum(1 for k in range(i + 1, i + h + 1) if days[k] in ev_days)
            rows.append((days[i], [1.0, realised(rets, i, 1), realised(rets, i, 5), realised(rets, i, 22),
                                   (v / 100) if v else realised(rets, i, 22), n_ev / h], fut, realised(rets, i, h)))
        res = {"by_year": []}
        errs = defaultdict(list)
        for y in sorted({r[0][:4] for r in rows}):
            if int(y) < 2018:
                continue
            train = [r for r in rows if r[0] < f"{y}-01-01"][:-h]
            test = [r for r in rows if r[0][:4] == y]
            if len(train) < 500 or not test:
                continue
            specs = {"HAR": [0, 1, 2, 3], "HAR+VIX": [0, 1, 2, 3, 4], "HAR+VIX+事件": [0, 1, 2, 3, 4, 5]}
            yr = {"year": y}
            for name, cols in specs.items():
                b = ols([[r[1][c] for c in cols] for r in train], [r[2] for r in train])
                pred = [max(.03, float(np.dot(b, [r[1][c] for c in cols]))) for r in test]
                e = [(p - r[2]) ** 2 for p, r in zip(pred, test)]
                errs[name] += e
                yr[name] = math.sqrt(sum(e) / len(e)) * 100
            e = [(r[3] - r[2]) ** 2 for r in test]
            errs["照搬上一期"] += e
            yr["照搬上一期"] = math.sqrt(sum(e) / len(e)) * 100
            res["by_year"].append(yr)
        res["rmse"] = {k: math.sqrt(sum(v) / len(v)) * 100 for k, v in errs.items() if v}
        var_y = [r[2] for r in rows if r[0] >= "2018-01-01"]
        m = sum(var_y) / len(var_y)
        sst = sum((x - m) ** 2 for x in var_y) / len(var_y)
        res["r2"] = {k: 1 - (v / 100) ** 2 / sst for k, v in res["rmse"].items()}
        b = ols([r[1] for r in rows], [r[2] for r in rows])
        res["coef"] = dict(zip(["常数", "昨天波动", "近 5 天波动", "近 22 天波动", "VIX", "期内事件密度"], [float(x) for x in b]))
        i = len(days) - 1
        v = vix_on(date.today().isoformat()) if vix_days else None
        upcoming = sum(1 for lab, evs in picked.items() for d in evs
                       if date.today().isoformat() <= d <= (date.today() + timedelta(days=int(h * 1.45))).isoformat())
        x = [1.0, realised(rets, i, 1), realised(rets, i, 5), realised(rets, i, 22), (v / 100) if v else realised(rets, i, 22),
             min(1.0, upcoming / h)]
        res["now"] = {"forecast": max(.03, float(np.dot(b, x))) * 100, "rv22": realised(rets, i, 22) * 100,
                      "vix": v, "events_ahead": upcoming, "asof": days[-1]}
        hist = sorted(r[2] for r in rows)
        res["now"]["pct"] = 100 * sum(1 for q in hist if q <= res["now"]["forecast"] / 100) / len(hist)
        out[str(h)] = res
    return out


# ---------------------------------------------------------------- regimes
def regimes(series):
    days = sorted(series["XJO"])
    xj = series["XJO"]
    import bisect

    def last(name, d):
        s = series.get(name)
        if not s:
            return None
        ks = SORTED[name]
        j = bisect.bisect_right(ks, d)
        return s[ks[j - 1]] if j > 0 else None

    def change(name, d, back):
        ks = SORTED.get(name)
        if not ks:
            return None
        j = bisect.bisect_right(ks, d)
        if j <= back:
            return None
        return series[name][ks[j - 1]], series[name][ks[j - 1 - back]]
    SORTED = {k: sorted(v) for k, v in series.items()}
    defs = {
        "VIX 水平": lambda d: (lambda v: None if v is None else "VIX<15 平静" if v < 15 else "VIX 15-25 正常" if v < 25 else "VIX≥25 恐慌")(last("VIX", d)),
        "美股 3 个月走势": lambda d: (lambda c: None if c is None else "美股涨>5%" if c[0] / c[1] - 1 > .05 else "美股跌>5%" if c[0] / c[1] - 1 < -.05 else "美股平稳")(change("SPX", d, 63)),
        "澳元 3 个月走势": lambda d: (lambda c: None if c is None else "澳元涨>3%" if c[0] / c[1] - 1 > .03 else "澳元跌>3%" if c[0] / c[1] - 1 < -.03 else "澳元平稳")(change("AUDUSD", d, 63)),
        "金价 3 个月走势": lambda d: (lambda c: None if c is None else "金价涨>5%" if c[0] / c[1] - 1 > .05 else "金价跌>5%" if c[0] / c[1] - 1 < -.05 else "金价平稳")(change("GOLD", d, 63)),
        "大盘 3 个月走势": lambda d: (lambda c: None if c is None else "大盘涨>5%" if c[0] / c[1] - 1 > .05 else "大盘跌>5%" if c[0] / c[1] - 1 < -.05 else "大盘平稳")(change("XJO", d, 63)),
    }
    out = []
    for name, fn in defs.items():
        buckets = defaultdict(lambda: {"r20": [], "r60": [], "v20": [], "d": []})
        for i in range(70, len(days) - 60, 5):
            d = days[i]
            if d < "2015-01-01":
                continue
            b = fn(d)
            if b is None:
                continue
            r20 = xj[days[i + 20]] / xj[d] - 1
            r60 = xj[days[i + 60]] / xj[d] - 1
            rr = [math.log(xj[days[k]] / xj[days[k - 1]]) for k in range(i + 1, i + 21)]
            v = math.sqrt(sum(x * x for x in rr) / 20 * 252)
            buckets[b]["r20"].append(r20)
            buckets[b]["r60"].append(r60)
            buckets[b]["v20"].append(v)
            buckets[b]["d"].append(d)
        rows = []
        for b, v in buckets.items():
            n = len(v["r20"])
            if n < 15:
                continue
            ins = [r for r, d in zip(v["r20"], v["d"]) if d < SPLIT]
            outs = [r for r, d in zip(v["r20"], v["d"]) if d >= SPLIT]
            eps, lastd = 0, ""
            for d in v["d"]:
                if not lastd or (date.fromisoformat(d) - date.fromisoformat(lastd)).days > 90:
                    eps += 1
                lastd = d
            rows.append({"bucket": b, "n": n, "episodes": eps, "r20": 100 * sum(v["r20"]) / n, "r60": 100 * sum(v["r60"]) / n,
                         "up20": 100 * sum(1 for x in v["r20"] if x > 0) / n, "vol20": 100 * sum(v["v20"]) / n,
                         "r20_in": 100 * sum(ins) / len(ins) if ins else None,
                         "r20_out": 100 * sum(outs) / len(outs) if outs else None})
        now = fn(days[-1])
        out.append({"factor": name, "rows": sorted(rows, key=lambda r: r["bucket"]), "now": now})
    return out


def main():
    t0 = time.time()
    syms = list(INDEX.values()) + list(MACRO.values())
    study.download(syms)
    series = {k: closes(v) for k, v in {**INDEX, **MACRO}.items()}
    series = {k: v for k, v in series.items() if len(v) > 500}
    print("  series:", {k: len(v) for k, v in series.items()})
    src = "eodhd"
    src = "calendar"
    picked = {}
    if os.environ.get("S4_FEED"):
        try:
            picked = classify(fetch_events())
        except Exception as e:
            print("  events feed failed:", type(e).__name__)
    if sum(len(v) for v in picked.values()) < 50:
        picked, src = fallback_events(), "calendar"
    print("  events:", {k: len(v) for k, v in picked.items()}, src)
    idx_series = {k: series[k] for k in INDEX if k in series}
    ev_rows, upcoming, base_abs = event_study(picked, idx_series)
    vf = vol_forecast(series, picked)
    rg = regimes(series)
    out = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M"), "minutes": round((time.time() - t0) / 60, 1),
                    "event_source": src, "series": {k: len(v) for k, v in series.items()},
                    "base_abs_move": base_abs},
           "events": ev_rows, "upcoming": upcoming, "vol_forecast": vf, "regimes": rg}
    p = Path(os.environ.get("STUDY4_OUT", study.DATA / "study4_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"round 4 done in {out['meta']['minutes']} min -> {p}")


if __name__ == "__main__":
    main()
