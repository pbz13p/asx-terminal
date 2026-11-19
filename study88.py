"""Round 88: ASX "shells" that change name / ASX code (like CND, formerly GLV and earlier names) -- who they are,
and whether a rename starts a big move more often than a random stock-month (Frank, 2026-11-19).

1. Renames: every announcement in our archive (2015 on) whose headline says change of name / company name / ASX code.
   One event per company per 60 days. Former name / code read from the announcement text ("formerly ...", "previously
   ...", "(ASX: XXX)") when it is there. Price history follows the company in EODHD under the current code.
2. Event study, rules fixed before results: buy at the next trading day's open after the rename announcement; outcome =
   round 80's labels: hit 10x / 5x within 3 years (peak close, with >= $10k/day traded around the peak and price
   >= 0.5c, i.e. a real tradable 10x), and the 12-month return net of round-71 costs. Only events with price >= 1c and
   20-day median >= $2k/day (round 80's buyable floor).
   Baseline: every month-end stock with the same floor (round 80's 2.4% ten-bagger rate), overall and matched by price
   band (<5c, 5-20c, 20c-$1, >$1) because shells are mostly penny stocks. Also: companies with 2+ renames since 2015.
Output: rename list per company (for checking), event stats, baseline stats. Encrypted; nothing printed in the log.
"""
import gzip
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

import study
import study80

OUT = Path(os.environ.get("STUDY88_OUT", "out/study88_results.json"))
ANN = Path("data/ann")
HEAD = re.compile(r"change (of|in) (company |the company'?s? )?name|name change|change of (asx )?(code|ticker)|"
                  r"new (company )?name|change of asx code|company name and (asx )?code|code change|rename|rebrand", re.I)
FORMER = re.compile(r"(?:formerly|previously)(?: known as)?\s+([A-Z][A-Za-z0-9&'.\- ]{2,60}?(?:Limited|Ltd|NL|Corporation|Group)\b)"
                    r"(?:\s*\((?:ASX[:\s]*)?([A-Z0-9]{3,4})\))?")


def band(p):
    return "<5c" if p < 0.05 else "5-20c" if p < 0.20 else "20c-$1" if p < 1 else ">$1"


def renames():
    out = {}
    for f in sorted(ANN.glob("*.json.gz")):
        d = json.loads(gzip.decompress(f.read_bytes()))
        code = f.name[:-8]
        ev = []
        for it in sorted(d.get("listed", []), key=lambda x: x.get("date", "")):
            if not HEAD.search(it.get("headline", "")):
                continue
            if ev and (np.datetime64(it["date"]) - np.datetime64(ev[-1]["date"])).astype(int) < 60:
                continue
            doc = d.get("docs", {}).get(it["ids"], {})
            m = FORMER.search(doc.get("text", "")[:6000])
            ev.append({"date": it["date"], "headline": it["headline"][:120],
                       "former": m.group(1).strip() if m else None, "former_code": m.group(2) if m and m.group(2) else None})
        if ev:
            out[code] = ev
    return out


def main():
    R = renames()
    cal = [r[0] for r in study.load("AXJO.INDX")]
    study80.study_last = cal[-1]
    ev_rows, base = [], defaultdict(lambda: [0, 0, 0, []])   # band -> [n, x10, x5, r12n list]
    for f in sorted(study.CACHE.glob("*.AU.json")):
        code = f.name[:-8]
        rows = study.load(f.name[:-5])
        if len(rows) < 60:
            continue
        rows = [r + (r[6] * r[5],) for r in rows]
        rows, _ = study80.fix_consolidations(rows)
        rows = study80.to_calendar(rows, cal)
        dates = [r[0] for r in rows]

        def floor_ok(i):
            v = [r[7] for r in rows[max(0, i - 19):i + 1]]
            return rows[i][6] >= 0.01 and study80.med(v) >= 2000

        for i in study80.month_ends(dates):
            if dates[i] < "2015-01-01" or not floor_ok(i):
                continue
            lab = study80.labels(rows, i)
            if lab is None or not lab["complete"]:
                continue
            for b in (band(rows[i][6]), "全部"):
                s = base[b]
                s[0] += 1
                s[1] += lab["x10"]
                s[2] += lab["x5"]
                if np.isfinite(lab["r12n"]):
                    s[3].append(lab["r12n"])
        n_ren = len(R.get(code, []))
        for e in R.get(code, []):
            i = next((k for k, d in enumerate(dates) if d > e["date"]), None)   # next trading day (open ~ previous close)
            if i is None or i < 1 or not floor_ok(i - 1):
                continue
            lab = study80.labels(rows, i - 1)
            if lab is None:
                continue
            ev_rows.append({"code": code, "date": e["date"], "price": rows[i - 1][6], "band": band(rows[i - 1][6]),
                            "x10": lab["x10"], "x5": lab["x5"], "r12n": lab["r12n"], "complete": lab["complete"],
                            "mult3y": round(lab["mult3y"], 2), "renames_total": n_ren})

    def summ(rs):
        rs = [r for r in rs if r["complete"]]
        if not rs:
            return {"n": 0}
        r12 = np.array([r["r12n"] for r in rs if np.isfinite(r["r12n"])])
        return {"n": len(rs), "10倍%": round(100 * np.mean([r["x10"] for r in rs]), 1), "5倍%": round(100 * np.mean([r["x5"] for r in rs]), 1),
                "12个月平均%": round(100 * float(r12.mean()), 1) if len(r12) else None,
                "12个月中位%": round(100 * float(np.median(r12)), 1) if len(r12) else None,
                "12个月命中%": round(100 * float((r12 > 0).mean()), 1) if len(r12) else None}

    res = {"method": __doc__.strip(), "companies_renamed": len(R), "rename_events": sum(len(v) for v in R.values()),
           "events_tested": len(ev_rows),
           "改名后": {"全部": summ(ev_rows), **{b: summ([r for r in ev_rows if r["band"] == b]) for b in ("<5c", "5-20c", "20c-$1", ">$1")},
                    "改名2次以上的公司": summ([r for r in ev_rows if r["renames_total"] >= 2]),
                    "2015-2021": summ([r for r in ev_rows if r["date"] < "2022"]), "2022起": summ([r for r in ev_rows if r["date"] >= "2022"])},
           "随便买(每月底)": {b: {"n": s[0], "10倍%": round(100 * s[1] / s[0], 1), "5倍%": round(100 * s[2] / s[0], 1),
                                "12个月平均%": round(100 * float(np.mean(s[3])), 1), "12个月中位%": round(100 * float(np.median(s[3])), 1)}
                            for b, s in base.items() if s[0]},
           "events": ev_rows, "renames": R}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False))
    print("[88] done", flush=True)


if __name__ == "__main__":
    main()
