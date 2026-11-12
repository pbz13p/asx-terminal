"""Round 19: short selling (ASIC daily aggregated short positions, free).

ASIC publishes, with a ~4 business-day delay, the % of each ASX stock that is sold short. Monthly
samples since 2015. Because of the delay, returns are measured from 5 trading days after the position
date (entry at that day's open) over the next 20 trading days, relative to the median tradable stock.
Groups: short level (% of shares) and 1-month change in short level. Both periods.
"""
import bisect
import csv
import io
import json
import os
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import study

URL = "https://download.asic.gov.au/short-selling/RR{d}-001-SSDailyAggShortPos.csv"


def fetch(day):
    req = urllib.request.Request(URL.format(d=day.strftime("%Y%m%d")), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    for enc in ("utf-16", "utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            if "Product" in text[:400]:
                break
        except UnicodeDecodeError:
            continue
    delim = "\t" if text[:400].count("\t") > text[:400].count(",") else ","
    out = {}
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        if len(row) < 5 or not row[1].strip() or row[1].strip().lower().startswith("product"):
            continue
        try:
            out[row[1].strip()] = float(row[4].strip().replace("%", ""))
        except ValueError:
            continue
    return out


def month_ends():
    d = date(2015, 1, 31)
    while d < date.today() - timedelta(days=45):
        yield d
        nm = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        d = (nm + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def main():
    snaps = {}
    for me in month_ends():
        for back in range(0, 7):
            day = me - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            try:
                s = fetch(day)
                if len(s) > 200:
                    snaps[day.isoformat()] = s
                    break
            except Exception:
                continue
    print("  ASIC snapshots:", len(snaps))
    keys = sorted(snaps)
    stocks = {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) > 100:
            stocks[f.name[:-8]] = rows
    groups = defaultdict(lambda: {"all": [], "2015–21": [], "2022后": []})
    lvl_dist = []
    for k_i, d in enumerate(keys):
        snap = snaps[d]
        prev = snaps[keys[k_i - 1]] if k_i else None
        samples = []
        for code, pct in snap.items():
            rows = stocks.get(code)
            if not rows:
                continue
            ds = [r[0] for r in rows]
            i = bisect.bisect_right(ds, d) - 1
            if i < 21 or i + 26 >= len(rows):
                continue
            turn = sorted(rows[k][6] * rows[k][5] for k in range(i - 20, i))[10]
            if rows[i][6] < .2 or turn < 5e5:
                continue
            e = rows[i + 5][1]
            if e <= 0:
                continue
            r = rows[i + 25][4] / e - 1
            chg = pct - prev[code] if prev and code in prev else None
            samples.append((pct, chg, r))
        if len(samples) < 50:
            continue
        med = sorted(x[2] for x in samples)[len(samples) // 2]
        per = "2015–21" if d < study.SPLIT else "2022后"
        for pct, chg, r in samples:
            lvl_dist.append(pct)
            lv = "空头<1%" if pct < 1 else "空头1–3%" if pct < 3 else "空头3–6%" if pct < 6 else "空头6–10%" if pct < 10 else "空头≥10%"
            keys_ = [lv]
            if chg is not None:
                keys_.append("1个月空头增加>2个百分点" if chg > 2 else "1个月空头减少>2个百分点" if chg < -2 else None)
            for k in keys_:
                if k:
                    groups[k]["all"].append(r - med)
                    groups[k][per].append(r - med)
    out = {"snapshots": len(snaps), "first": keys[0] if keys else None, "last": keys[-1] if keys else None, "groups": {}}
    for k, g in groups.items():
        out["groups"][k] = {p: {"n": len(v), "ex20": 100 * sum(v) / len(v) if v else None,
                                "win": 100 * sum(1 for x in v if x > 0) / len(v) if v else None} for p, v in g.items()}
    p = Path(os.environ.get("STUDY19_OUT", study.DATA / "study19_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
