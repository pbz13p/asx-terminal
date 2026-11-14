"""Round 22 / daily scan: which stocks meet the research rules right now.

Run every weekday after the ASX close by GitHub Actions. Updates the price cache with EODHD's bulk
last-day endpoint (one call per day instead of one per stock), then lists:
  - 52-week-high breakouts in the last 5 sessions (round 21 rules, with volume)
  - results-season gap-ups that held (round 10) and gap-downs to avoid (rounds 10-11), last 5 sessions
  - commodity themes where the stocks lag their commodity (round 17)
  - ASX 200 down streak (round 13/14)
Writes scan.json.
"""
import json
import math
import os
import time
from datetime import date, timedelta
from pathlib import Path

import study
import study10

THEMES = {
    "黄金": ("GLD.US", ["NST", "EVN", "RRL", "PRU", "RMS", "WGX", "GOR", "DEG", "CMM", "RSG", "SBM", "WAF", "PDI", "BGL", "GMD", "VAU", "SLR"]),
    "石油天然气": ("USO.US", ["WDS", "STO", "BPT", "KAR", "STX", "CVN", "SXY"]),
    "铜": ("CPER.US", ["SFR", "29M", "AIS", "HCH", "C6C", "AZY", "S32", "SND", "CYM"]),
    "锂": ("LIT.US", ["PLS", "MIN", "IGO", "LTR", "CXO", "SYA", "LKE", "VUL", "GL1", "INR"]),
    "铀": ("URA.US", ["PDN", "BOE", "DYL", "LOT", "BMN", "PEN", "AGE", "DEV", "NXG"]),
}


ANN_URL = "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements?page={p}&itemsPerPage=100"
RULES = [  # (bucket, words in type or headline) — order matters
    ("收购/协议安排", ("scheme implementation", "scheme booklet", "takeover bid", "bidder's statement", "bidders statement",
                   "target's statement", "targets statement", "takeover offer", "non-binding indicative", "indicative proposal",
                   "scheme meeting", "unsolicited proposal", "binding scheme", "acquisition proposal", "off-market takeover",
                   "takeover announcements", "scheme announcements")),
    ("配股/融资", ("placement", "capital raising", "entitlement offer", "share purchase plan", "rights issue", "institutional offer",
               "proposed issue of securities")),
    ("停牌", ("trading halt", "suspension from quotation", "voluntary suspension")),
]


def fetch_announcements(days_back=3):
    import urllib.request
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    items = []
    for page in range(0, 40):
        try:
            req = urllib.request.Request(ANN_URL.format(p=page), headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)["data"]["items"]
        except Exception as e:
            print("  announcements page", page, type(e).__name__)
            break
        if not data:
            break
        items += data
        if data[-1].get("date", "")[:10] < cutoff:
            break
    out = {b: [] for b, _ in RULES}
    seen = set()
    for it in items:
        if it.get("date", "")[:10] < cutoff:
            continue
        text = (" ".join(it.get("announcementTypes") or []) + " " + (it.get("headline") or "")).lower()
        for bucket, words in RULES:
            if any(w in text for w in words):
                key = (bucket, it.get("symbol"), it.get("headline"))
                if key in seen:
                    break
                seen.add(key)
                info = (it.get("companyInfo") or [{}])
                out[bucket].append({"code": it.get("symbol"), "time": it.get("date", "")[:16].replace("T", " "),
                                    "headline": it.get("headline"), "sensitive": bool(it.get("isPriceSensitive")),
                                    "type": ", ".join(it.get("announcementTypes") or []),
                                    "name": info[0].get("displayName") if info else None})
                break
    print("  announcements:", len(items), {k: len(v) for k, v in out.items()})
    return out


def refresh_cache():
    """Append the last few sessions from the bulk endpoint to every cached file."""
    study.CACHE.mkdir(parents=True, exist_ok=True)
    added = 0
    for back in range(6, -1, -1):
        d = date.today() - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        rows = study.api("eod-bulk-last-day/AU", date=d.isoformat())
        if not isinstance(rows, list):
            continue
        for r in rows:
            code = r.get("code")
            if not code or not r.get("close"):
                continue
            f = study.CACHE / f"{code}.AU.json"
            if not f.exists():
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            if data and data[-1][0] >= r["date"]:
                continue
            data.append([r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                         r.get("adjusted_close") or r.get("close"), r.get("volume")])
            f.write_text(json.dumps(data), encoding="utf-8")
            added += 1
    print("  bulk rows added:", added)


def rocket_screen(last_day, calendar):
    """Round 66 rule: fell >= 50% from its 3-year high, value traded (20-day median) >= 2x its 1-year median, and
    back above the 200-day line. Historically 34% of such stocks doubled at some point within 12 months, ~20% lost
    half; average basket +30% (after 3% costs), vs +10% for all small caps."""
    out = []
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 260 or rows[-1][0] < (date.fromisoformat(last_day) - timedelta(days=7)).isoformat():
            continue  # thinly traded stocks often skip days
        # put the stock on the ASX 200 trading calendar like the backtest: no trade = $0 value, price carried
        by = {r[0]: r for r in rows}
        cal = [d for d in calendar if d >= rows[0][0]]
        cl, val, last = [], [], None
        for d in cal:
            r = by.get(d)
            if r:
                last = r
            cl.append(last[4] if last else None)
            val.append(r[6] * r[5] if r else 0.0)
        k0 = next((k for k, x in enumerate(cl) if x is not None), len(cl))
        cl, val, cal = cl[k0:], val[k0:], cal[k0:]
        if len(cl) < 260:
            continue
        rows = [(d,) for d in cal]
        raw = by[max(by)][6]
        v60 = sorted(val[-60:])[30]
        if raw < 0.005 or v60 < 1e4:
            continue

        def ok(i):
            if i < 252:
                return False
            hi3 = max(cl[max(0, i - 755):i + 1])
            ma200 = sum(cl[i - 199:i + 1]) / 200
            v20 = sorted(val[i - 19:i + 1])[10]
            v250 = sorted(val[max(0, i - 249):i + 1])[len(val[max(0, i - 249):i + 1]) // 2]
            return cl[i] / hi3 - 1 < -0.5 and cl[i] > ma200 and v250 > 0 and v20 / v250 > 2, hi3, ma200, v20, v250
        n = len(rows)
        today = ok(n - 1)
        if not today or not today[0]:
            continue
        first = n - 1
        while first - 1 >= n - 120 and (lambda x: x and x[0])(ok(first - 1)):
            first -= 1
        _, hi3, ma200, v20, v250 = today
        out.append({"code": f.name[:-8], "price": raw, "from_high_3y": round(100 * (cl[-1] / hi3 - 1)),
                    "above_ma200": round(100 * (cl[-1] / ma200 - 1)), "value_x": round(v20 / v250, 1),
                    "value_k": round(v60 / 1e3), "since": rows[first][0], "days": n - 1 - first,
                    "ret_since": round(100 * (cl[-1] / cl[first] - 1), 1),
                    "tradable": raw >= 0.02 and v60 >= 3e4})
    out.sort(key=lambda x: (x["days"] > 20, -x["value_x"]))
    try:
        import volml
        for x in out[:60]:
            ci = volml.company_info(x["code"])
            x.update({"name": ci.get("displayName"), "sector": ci.get("sector"), "mcap": ci.get("marketCap"),
                      "about": (ci.get("description") or "")[:200]})
    except Exception as e:
        print("  company info failed:", type(e).__name__)
    return out[:60]


def watch_screen(last_day, calendar):
    """Stocks pbz watches (watchlist.txt, one code per line). Alerts: unusual value traded, crossing the 200-day line,
    monthly MACD histogram turning positive (round 70: the only divergence signal that held up), new announcements."""
    p = Path("watchlist.txt")
    codes = [c.strip().upper() for c in (p.read_text().split() if p.exists() else ["CND"]) if c.strip()]
    out = []
    for code in codes:
        rows = study.load(f"{code}.AU")
        if len(rows) < 260:
            continue
        by = {r[0]: r for r in rows}
        cal = [d for d in calendar if d >= rows[0][0]]
        cl, val, last = [], [], None
        for d in cal:
            r = by.get(d)
            if r:
                last = r
            cl.append(last[4] if last else None)
            val.append(r[6] * r[5] if r else 0.0)
        med = lambda a: sorted(a)[len(a) // 2]
        v60 = med(val[-61:-1]) or 1.0
        ma200, ma200_y = sum(cl[-200:]) / 200, sum(cl[-201:-1]) / 200
        months, seen = [], set()
        for d, c in zip(cal, cl):
            if d[:7] in seen:
                months[-1] = c
            else:
                seen.add(d[:7])
                months.append(c)
        e12 = e26 = sig = None
        hist = []
        for k, c in enumerate(months):
            e12 = c if e12 is None else e12 + 2 / 13 * (c - e12)
            e26 = c if e26 is None else e26 + 2 / 27 * (c - e26)
            m = (e12 - e26) / e26
            sig = m if sig is None else sig + 0.2 * (m - sig)
            hist.append(m - sig)
        x = {"code": code, "price": rows[-1][6], "date": rows[-1][0], "chg_1d": round(100 * (cl[-1] / cl[-2] - 1), 1),
             "value_today_x": round(val[-1] / v60, 1), "value_5d_x": round(sum(val[-5:]) / 5 / v60, 1),
             "above_ma200": round(100 * (cl[-1] / ma200 - 1), 1), "macd_m_hist_pos": hist[-1] > 0,
             "macd_m_hist_months_pos": next((k for k in range(len(hist)) if hist[-1 - k] <= 0), len(hist)), "alerts": []}
        if val[-1] > 3 * v60:
            x["alerts"].append(f"今天成交额是平时的 {x['value_today_x']} 倍")
        elif sum(val[-5:]) / 5 > 2 * v60:
            x["alerts"].append(f"近 5 天成交额是平时的 {x['value_5d_x']} 倍")
        if cl[-1] > ma200 and cl[-2] <= ma200_y:
            x["alerts"].append("今天站上 200 日线")
        if hist[-1] > 0 and len(hist) > 1 and hist[-2] <= 0:
            x["alerts"].append("月 MACD 柱子刚由负转正")
        try:
            import study67
            anns = [a for a in study67.listing(code, [int(last_day[:4])])
                    if a["date"] >= (date.fromisoformat(last_day) - timedelta(days=3)).isoformat()]
            x["announcements"] = [{"date": a["date"], "headline": a["headline"], "sensitive": a["sensitive"]} for a in anns[:10]]
            for a in anns[:5]:
                x["alerts"].append(("⚠ 价格敏感公告：" if a["sensitive"] else "新公告：") + a["headline"][:80])
        except Exception as e:
            print("  watch announcements failed:", code, type(e).__name__)
        out.append(x)
    return out


def main():
    t0 = time.time()
    refresh_cache()
    study.download(["AXJO.INDX", "AUDUSD.FOREX"] + [t[0] for t in THEMES.values()])
    for sym in ["AXJO.INDX", "AUDUSD.FOREX"] + [t[0] for t in THEMES.values()]:  # force same-day refresh
        f = study.CACHE / f"{sym}.json"
        if f.exists() and time.time() - f.stat().st_mtime > 6 * 3600:
            f.unlink()
    study.download(["AXJO.INDX", "AUDUSD.FOREX"] + [t[0] for t in THEMES.values()])
    xj = study.load("AXJO.INDX")
    last_day = xj[-1][0]
    breakouts, gap_up, gap_dn, halts, sleeve = [], [], [], [], []
    xidx = {r[0]: k for k, r in enumerate(xj)}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 260 or rows[-1][0] < (date.fromisoformat(last_day) - timedelta(days=4)).isoformat():
            continue
        code = f.name[:-8]
        n = len(rows)
        cl = [r[4] for r in rows]
        for i in range(max(253, n - 5), n):
            d, o, h, l, c, v, raw = rows[i]
            if raw < .2:
                continue
            vols = sorted(r[5] for r in rows[i - 20:i])
            turn = sorted(r[6] * r[5] for r in rows[i - 20:i])[10]
            if turn < 2e5 or vols[10] <= 0:
                continue
            vx = v / vols[10]
            prev_hi = max(cl[i - 252:i])
            if turn >= 5e5 and c > prev_hi and min(cl[i - 60:i]) <= prev_hi * .85 and vx >= 2:
                breakouts.append({"code": code, "date": d, "close": raw, "vol_x": round(vx, 1), "turnover": round(turn),
                                  "since": round(100 * (cl[-1] / c - 1), 1)})
            pc = rows[i - 1][4]
            pd_ = rows[i - 1][0]
            missed = (xidx[d] - xidx[pd_] - 1) if d in xidx and pd_ in xidx else 0
            if 1 <= missed <= 5 and pc > 0 and o > 0 and o / pc - 1 <= -.05 and vx >= 3:
                halts.append({"code": code, "date": d, "gap": round(100 * (o / pc - 1), 1), "halted": missed,
                              "close": raw, "turnover": round(turn), "since": round(100 * (cl[-1] / c - 1), 1)})
            if pc > 0 and o > 0 and vx >= 3 and study10.season(d):
                gap = o / pc - 1
                item = {"code": code, "date": d, "gap": round(100 * gap, 1), "day": round(100 * (c / o - 1), 1),
                        "close": raw, "turnover": round(turn), "since": round(100 * (cl[-1] / c - 1), 1)}
                if gap >= .05 and c > o and turn >= 5e5:
                    gap_up.append(item)
                if gap >= .08 and c > o and 2e5 <= turn < 2e6:
                    sleeve.append(item)
                elif gap <= -.05 and turn >= 5e5:
                    gap_dn.append(item)
    # themes
    aud = {r[0]: r[4] for r in study.load("AUDUSD.FOREX")}
    xjd = {r[0]: r[4] for r in xj}
    themes = {}
    import bisect
    for theme, (etf, members) in THEMES.items():
        e = {r[0]: r[4] for r in study.load(etf)}
        ek, ak = sorted(e), sorted(aud)
        mem = {m: {r[0]: r[4] for r in study.load(f"{m}.AU")} for m in members}
        days = [d for d in sorted(xjd) if d >= (date.today() - timedelta(days=3 * 365)).isoformat()]
        b, u = [], []

        def us(d):
            j, k = bisect.bisect_left(ek, d) - 1, bisect.bisect_left(ak, d) - 1
            return e[ek[j]] / aud[ak[k]] if j >= 0 and k >= 0 else None
        for i in range(1, len(days)):
            d, p = days[i], days[i - 1]
            rs = [s[d] / s[p] - 1 for s in mem.values() if d in s and p in s and s[p] > 0]
            u1, u0 = us(d), us(p)
            if len(rs) < 3 or not u1 or not u0:
                continue
            b.append(sum(rs) / len(rs) - (xjd[d] / xjd[p] - 1))
            u.append(u1 / u0 - 1)
        if len(b) < 100:
            continue
        mb, mu = sum(b) / len(b), sum(u) / len(u)
        bt = sum((x - mu) * (y - mb) for x, y in zip(u, b)) / sum((x - mu) ** 2 for x in u)
        cum = lambda a, i, j: math.prod(1 + x for x in a[i:j]) - 1
        res = [cum(b, i - 20, i) - bt * cum(u, i - 20, i) for i in range(20, len(b) + 1)]
        sd = math.sqrt(sum(x * x for x in res) / len(res))
        themes[theme] = {"z": round(res[-1] / sd, 2), "commodity_20d": round(100 * cum(u, len(u) - 20, len(u)), 1),
                         "stocks_vs_xjo_20d": round(100 * cum(b, len(b) - 20, len(b)), 1), "etf": etf}
    # market breadth (round 41): share of liquid stocks above their 200-day average
    above = tot = 0
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 220 or rows[-1][0] != last_day:
            continue
        turn = sorted(r[6] * r[5] for r in rows[-20:])[10]
        if turn < 1e6:
            continue
        tot += 1
        above += rows[-1][4] > sum(r[4] for r in rows[-200:]) / 200
    breadth = round(100 * above / tot, 1) if tot >= 100 else None
    xc = [r[4] for r in xj]
    downs = 0
    while downs < len(xc) - 1 and xc[-1 - downs] < xc[-2 - downs]:
        downs += 1
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "last_day": last_day,
           "xjo_down_days": downs, "breadth": breadth, "breadth_n": tot, "breakouts": sorted(breakouts, key=lambda x: -x["turnover"])[:40],
           "season_gap_up": sorted(gap_up, key=lambda x: -x["turnover"])[:30],
           "season_gap_down": sorted(gap_dn, key=lambda x: -x["turnover"])[:30], "themes": themes,
           "halt_reopen_down": sorted(halts, key=lambda x: -x["turnover"])[:30],
           "sleeve_candidates": sorted(sleeve, key=lambda x: -x["turnover"])[:20],
           "announcements": fetch_announcements(),
           "rockets": rocket_screen(last_day, [r[0] for r in xj]),
           "watch": watch_screen(last_day, [r[0] for r in xj]),
           "seconds": round(time.time() - t0)}
    # keep a growing archive of classified announcements for future event studies
    hist_path = Path("out/announcements_history.json")
    try:
        hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else []
    except Exception:
        hist = []
    keys = {(h["bucket"], h["code"], h["time"], h["headline"]) for h in hist}
    for bucket, rows_ in out["announcements"].items():
        for a in rows_:
            k = (bucket, a["code"], a["time"], a["headline"])
            if k not in keys:
                keys.add(k)
                hist.append({"bucket": bucket, **a})
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    print("  announcement archive:", len(hist))
    p = Path(os.environ.get("STUDY22_OUT", study.DATA / "scan.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
