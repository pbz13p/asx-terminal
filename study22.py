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
    breakouts, gap_up, gap_dn = [], [], []
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
            if turn < 5e5 or vols[10] <= 0:
                continue
            vx = v / vols[10]
            prev_hi = max(cl[i - 252:i])
            if c > prev_hi and min(cl[i - 60:i]) <= prev_hi * .85 and vx >= 2:
                breakouts.append({"code": code, "date": d, "close": raw, "vol_x": round(vx, 1), "turnover": round(turn),
                                  "since": round(100 * (cl[-1] / c - 1), 1)})
            pc = rows[i - 1][4]
            if pc > 0 and o > 0 and vx >= 3 and study10.season(d):
                gap = o / pc - 1
                item = {"code": code, "date": d, "gap": round(100 * gap, 1), "day": round(100 * (c / o - 1), 1),
                        "close": raw, "turnover": round(turn), "since": round(100 * (cl[-1] / c - 1), 1)}
                if gap >= .05 and c > o:
                    gap_up.append(item)
                elif gap <= -.05:
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
    xc = [r[4] for r in xj]
    downs = 0
    while downs < len(xc) - 1 and xc[-1 - downs] < xc[-2 - downs]:
        downs += 1
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "last_day": last_day,
           "xjo_down_days": downs, "breakouts": sorted(breakouts, key=lambda x: -x["turnover"])[:40],
           "season_gap_up": sorted(gap_up, key=lambda x: -x["turnover"])[:30],
           "season_gap_down": sorted(gap_dn, key=lambda x: -x["turnover"])[:30], "themes": themes,
           "seconds": round(time.time() - t0)}
    p = Path(os.environ.get("STUDY22_OUT", study.DATA / "scan.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
