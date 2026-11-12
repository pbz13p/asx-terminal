"""Round 7: free official macro data (RBA, FRED) and market-implied surprises.

No consensus feed is needed: the 2-year government bond yield move on the day of a release measures how
surprising it was (yields jump on a hawkish surprise, drop on a dovish one). We then ask what ASX 200
and its sectors do over the next day / week / month after hawkish vs dovish surprises — that part is
tradeable because it starts after the event day's close.

Also: yield-curve and yield-trend regimes vs forward ASX 200 returns, and probes of free calendar feeds.
"""
import csv
import io
import json
import math
import os
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import study
import study4

UA = {"User-Agent": "Mozilla/5.0 (research script)", "Accept": "*/*"}


def get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for a in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            err = e
            time.sleep(3 * (a + 1))
    raise err


def rba_table(name):
    """Parse an RBA statistical-table CSV into {series_id: {iso_date: value}}."""
    text = get(f"https://www.rba.gov.au/statistics/tables/csv/{name}-data.csv")
    rows = list(csv.reader(io.StringIO(text)))
    ids = next(r for r in rows if r and r[0].strip().lower() == "series id")
    out = {s: {} for s in ids[1:] if s}
    start = rows.index(ids) + 1
    for r in rows[start:]:
        if not r or not r[0].strip():
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%d-%b-%Y").date().isoformat()
        except ValueError:
            try:
                d = datetime.strptime(r[0].strip(), "%b-%Y").date().isoformat()
            except ValueError:
                continue
        for s, v in zip(ids[1:], r[1:]):
            if s and v.strip():
                try:
                    out[s][d] = float(v)
                except ValueError:
                    pass
    return out


def fred(series_id):
    text = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}")
    out = {}
    for r in list(csv.reader(io.StringIO(text)))[1:]:
        if len(r) >= 2 and r[1] not in (".", ""):
            out[r[0]] = float(r[1])
    return out


def probe(name, fn):
    try:
        v = fn()
        return {"name": name, "ok": True, "detail": v}
    except Exception as e:
        return {"name": name, "ok": False, "detail": f"{type(e).__name__}: {str(e)[:120]}"}


def change_on(s, keys, d):
    import bisect
    i = bisect.bisect_left(keys, d)
    if i >= len(keys) or keys[i] != d or i == 0:
        return None
    return s[d] - s[keys[i - 1]]


def surprise_study(events, ylds, idx, label_country):
    """events: {label: [dates]}; ylds: yield series to measure surprise; idx: {name: {date: close}}."""
    import bisect
    ykeys = sorted(ylds)
    days = sorted(idx["XJO"])
    out = []
    for label, ds in events.items():
        country = label_country(label)
        rows = []
        for d in ds:
            if d < "2015-01-01" or d > date.today().isoformat():
                continue
            dy = change_on(ylds, ykeys, d)
            ad = study4.asx_day_for(d, country, days)
            if dy is None or ad is None:
                continue
            i = days.index(ad)
            if i < 1 or i + 21 >= len(days):
                continue
            xj = idx["XJO"]
            rec = {"d": d, "dy": dy * 100,  # basis points
                   "day": xj[ad] / xj[days[i - 1]] - 1,
                   "w": xj[days[i + 5]] / xj[ad] - 1, "m": xj[days[i + 20]] / xj[ad] - 1,
                   "sec_day": {}, "sec_w": {}}
            for k, s in idx.items():
                if k != "XJO" and ad in s and days[i - 1] in s and days[i + 5] in s:
                    rec["sec_day"][k] = s[ad] / s[days[i - 1]] - 1
                    rec["sec_w"][k] = s[days[i + 5]] / s[ad] - 1
            rows.append(rec)
        if len(rows) < 15:
            continue
        rows.sort(key=lambda r: r["dy"])
        n3 = len(rows) // 3
        groups = {"鸽派意外(收益率跌最多 1/3)": rows[:n3], "中性": rows[n3:-n3], "鹰派意外(收益率涨最多 1/3)": rows[-n3:]}

        def agg(g):
            n = len(g)
            mean = lambda xs: 100 * sum(xs) / len(xs) if xs else None
            return {"n": n, "dy": sum(r["dy"] for r in g) / n, "day": mean([r["day"] for r in g]),
                    "w": mean([r["w"] for r in g]), "m": mean([r["m"] for r in g]),
                    "up_w": 100 * sum(1 for r in g if r["w"] > 0) / n,
                    "sec_day": {k: mean([r["sec_day"][k] for r in g if k in r["sec_day"]]) for k in idx if k != "XJO"},
                    "sec_w": {k: mean([r["sec_w"][k] for r in g if k in r["sec_w"]]) for k in idx if k != "XJO"},
                    "out_w": mean([r["w"] for r in g if r["d"] >= study.SPLIT]),
                    "in_w": mean([r["w"] for r in g if r["d"] < study.SPLIT])}
        # correlation of yield surprise with same-day ASX move
        xs, ys = [r["dy"] for r in rows], [r["day"] for r in rows]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        cor = cov / math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
        out.append({"event": label, "n": len(rows), "corr_day": cor,
                    "groups": {k: agg(v) for k, v in groups.items()},
                    "latest": [{"d": r["d"], "dy": round(r["dy"], 1), "day": round(100 * r["day"], 2)}
                               for r in sorted(rows, key=lambda r: r["d"])[-6:]]})
    return out


def regimes(xj, series):
    """Forward ASX 200 returns by bond-market state (sampled weekly)."""
    import bisect
    days = sorted(xj)
    keys = {k: sorted(v) for k, v in series.items()}

    def val(k, d, back=0):
        ks = keys[k]
        j = bisect.bisect_right(ks, d) - 1 - back
        return series[k][ks[j]] if j >= 0 else None
    defs = {
        "澳债 10 年-2 年 利差": ("slope", lambda d: (lambda a, b: None if a is None or b is None else
                                                   "倒挂(<0)" if a - b < 0 else "平坦(0-1%)" if a - b < 1 else "陡峭(>1%)")(val("AU10", d), val("AU2", d))),
        "澳债 2 年 3 个月变化": ("au2chg", lambda d: (lambda a, b: None if a is None or b is None else
                                                    "加息预期升(>+0.5%)" if a - b > .5 else "降息预期升(<-0.5%)" if a - b < -.5 else "基本不变")(val("AU2", d), val("AU2", d, 63))),
        "美债 10 年-2 年 利差": ("usslope", lambda d: (lambda a: None if a is None else
                                                    "倒挂(<0)" if a < 0 else "平坦(0-1%)" if a < 1 else "陡峭(>1%)")(val("T10Y2Y", d))),
        "美债 10 年 3 个月变化": ("us10chg", lambda d: (lambda a, b: None if a is None or b is None else
                                                    "收益率升>0.5%" if a - b > .5 else "收益率降>0.5%" if a - b < -.5 else "基本不变")(val("DGS10", d), val("DGS10", d, 63))),
    }
    out = []
    for name, (_, fn) in defs.items():
        b = defaultdict(lambda: {"r20": [], "r60": [], "d": []})
        for i in range(70, len(days) - 60, 5):
            d = days[i]
            if d < "2015-01-01":
                continue
            try:
                g = fn(d)
            except KeyError:
                g = None
            if g is None:
                continue
            b[g]["r20"].append(xj[days[i + 20]] / xj[d] - 1)
            b[g]["r60"].append(xj[days[i + 60]] / xj[d] - 1)
            b[g]["d"].append(d)
        rows = []
        for g, v in b.items():
            n = len(v["r20"])
            if n < 12:
                continue
            ins = [x for x, d in zip(v["r20"], v["d"]) if d < study.SPLIT]
            outs = [x for x, d in zip(v["r20"], v["d"]) if d >= study.SPLIT]
            rows.append({"bucket": g, "n": n, "r20": 100 * sum(v["r20"]) / n, "r60": 100 * sum(v["r60"]) / n,
                         "up20": 100 * sum(1 for x in v["r20"] if x > 0) / n,
                         "in": 100 * sum(ins) / len(ins) if ins else None, "out": 100 * sum(outs) / len(outs) if outs else None})
        try:
            now = fn(days[-1])
        except KeyError:
            now = None
        out.append({"factor": name, "rows": rows, "now": now})
    return out


def main():
    res = {"probes": []}
    data = {}
    for name, fn in [("RBA F2 债券收益率", lambda: rba_table("f2")), ("RBA F1 货币市场利率", lambda: rba_table("f1")),
                     ("RBA G1 CPI", lambda: rba_table("g1"))]:
        p = probe(name, fn)
        if p["ok"]:
            data[name] = p["detail"]
            p["detail"] = {k: len(v) for k, v in p["detail"].items()}
        res["probes"].append(p)
    for sid in ("DGS2", "DGS10", "T10Y2Y", "VIXCLS", "CPIAUCSL", "PAYEMS", "DFF"):
        p = probe(f"FRED {sid}", lambda sid=sid: fred(sid))
        if p["ok"]:
            data[sid] = p["detail"]
            p["detail"] = f"{len(p['detail'])} 条, 最新 {max(p['detail'])}"
        res["probes"].append(p)
    res["probes"].append(probe("Forex Factory 本周日历", lambda: len(json.loads(get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")))))
    res["probes"].append(probe("Nasdaq 经济日历(含预期值)", lambda: json.loads(get(
        "https://api.nasdaq.com/api/calendar/economicevents?date=2024-07-31", {"Accept": "application/json"}))["data"]["rows"][:3]))
    res["probes"].append(probe("ASX RBA Rate Tracker", lambda: len(get("https://www.asx.com.au/markets/trade-our-derivatives-market/futures-market/rba-rate-tracker"))))
    res["probes"].append(probe("ABS Data API CPI", lambda: get("https://data.api.abs.gov.au/rest/data/CPI/1.10001.10.50.Q?startPeriod=2024-Q1",
                                                              {"Accept": "application/vnd.sdmx.data+csv"})[:300]))
    print(json.dumps(res["probes"], ensure_ascii=False, indent=1)[:3000])

    study.download(list(study4.INDEX.values()))
    idx = {k: {r[0]: r[4] for r in study.load(v)} for k, v in study4.INDEX.items()}
    idx = {k: v for k, v in idx.items() if len(v) > 500}
    f2 = data.get("RBA F2 债券收益率", {})
    au2 = f2.get("FCMYGBAG2D") or {}
    au10 = f2.get("FCMYGBAG10D") or {}
    cal = study4.fallback_events()
    if au2:
        au_events = {k: list(v) for k, v in cal.items() if not k.startswith("美")}
        res["au_surprise"] = surprise_study(au_events, au2, idx, lambda l: "AU")
    if data.get("DGS2"):
        us_events = {k: list(v) for k, v in cal.items() if k.startswith("美")}
        res["us_surprise"] = surprise_study(us_events, data["DGS2"], idx, lambda l: "US")
    ser = {"AU2": au2, "AU10": au10, "T10Y2Y": data.get("T10Y2Y", {}), "DGS10": data.get("DGS10", {})}
    ser = {k: v for k, v in ser.items() if v}
    res["regimes"] = regimes(idx["XJO"], ser) if ser else []
    res["now"] = {k: (max(v), v[max(v)]) for k, v in ser.items()}
    p = Path(os.environ.get("STUDY7_OUT", study.DATA / "study7_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    print("round 7 done")


if __name__ == "__main__":
    main()
