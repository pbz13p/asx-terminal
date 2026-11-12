"""Round 37: real takeover/scheme deals from ASX announcement history -> merger-arbitrage returns.

1. Candidates: stocks in the price cache that jumped >= 15% on >= 5x volume and later delisted, or are
   still trading (to include failed deals). For each, fetch that year's (and next year's) announcement
   list from asx.com.au (legacy yearly list) — a few hundred requests, spaced out.
2. A deal is confirmed when a headline mentions a scheme implementation deed / takeover bid / bidder's
   statement within 10 days after the jump.
3. Arbitrage trade: buy at the close 5 sessions after the jump; exit at the last close before delisting
   (completed) or after 400 sessions; deal 'broke' if it never delisted and fell >= 15% below entry.
Output: per-deal results, completion rate, median spread, annualised return, loss on breaks.
"""
import json
import math
import os
import re
import time
from datetime import date
from pathlib import Path
import urllib.request

import study

LIST_URL = "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode={c}&timeframe=Y&year={y}"
DEAL_WORDS = ("scheme implementation", "implementation deed", "takeover bid", "bidder's statement", "bidders statement",
              "takeover offer", "scheme booklet", "target's statement", "targets statement", "binding scheme")


def fetch_list(code, year):
    req = urllib.request.Request(LIST_URL.format(c=code, y=year), headers={"Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=40) as r:
        html = r.read().decode("utf-8", "replace")
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", tr)
        if not m:
            continue
        text = re.sub(r"<[^>]+>", " ", tr)
        text = re.sub(r"\s+", " ", text).strip()
        out.append((f"{m.group(3)}-{m.group(2)}-{m.group(1)}", text))
    return out


def main():
    last_day = max(r[0] for r in study.load("AXJO.INDX"))
    cands = []
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 80:
            continue
        code = f.name[:-8]
        for i in range(21, len(rows) - 6):
            d, o, h, l, c, v, raw = rows[i]
            pc = rows[i - 1][4]
            vols = sorted(r[5] for r in rows[i - 20:i])
            turn = sorted(r[6] * r[5] for r in rows[i - 20:i])[10]
            if d < "2015-01-01" or pc <= 0 or vols[10] <= 0 or turn < 2e5 or raw < .1:
                continue
            if c / pc - 1 >= .15 and v >= 5 * vols[10]:
                moves = [abs(rows[k][4] / rows[k - 1][4] - 1) for k in range(i + 1, i + 6)]
                delists_soon = rows[-1][0] < last_day and len(rows) - 1 - i <= 400
                if max(moves) < .03 or delists_soon:
                    cands.append((code, i, d))
    # keep one candidate per stock per 200 sessions
    cands.sort()
    pruned, last = [], {}
    for code, i, d in cands:
        if code in last and i - last[code] < 200:
            continue
        last[code] = i
        pruned.append((code, i, d))
    print("  jump candidates:", len(pruned))
    cache = {}
    deals = []
    for code, i, d in pruned:
        y = int(d[:4])
        anns = []
        for yy in (y,):
            key = (code, yy)
            if key not in cache:
                try:
                    cache[key] = fetch_list(code, yy)
                except Exception:
                    cache[key] = []
                time.sleep(0.4)
            anns += cache[key]
        if not anns:
            continue
        dd = date.fromisoformat(d)
        hit = [t for a, t in anns if 0 <= (date.fromisoformat(a) - dd).days <= 10 and any(w in t.lower() for w in DEAL_WORDS)]
        pre = [t for a, t in anns if -3 <= (date.fromisoformat(a) - dd).days <= 0 and any(w in t.lower() for w in ("takeover", "scheme", "proposal", "acquisition"))]
        if not hit and not pre:
            continue
        rows = study.load(f"{code}.AU")
        e_i = i + 5
        if e_i >= len(rows):
            continue
        entry = rows[e_i][4]
        end_i = min(len(rows) - 1, e_i + 400)
        delisted = rows[-1][0] < last_day and len(rows) - 1 <= e_i + 400
        exit_ = rows[end_i][4]
        worst = min(r[4] for r in rows[e_i:end_i + 1]) / entry - 1
        r = exit_ / entry - 1
        hold = max(end_i - e_i, 5)
        deals.append({"code": code, "date": d, "jump": round(100 * (rows[i][4] / rows[i - 1][4] - 1), 1), "ret": round(100 * r, 2),
                      "days": hold, "completed": delisted, "worst": round(100 * worst, 1),
                      "ann": round(100 * ((1 + r) ** (252 / hold) - 1), 1), "headline": (hit or pre)[0][:120]})
    comp = [x for x in deals if x["completed"]]
    notc = [x for x in deals if not x["completed"]]
    med = lambda a, k: sorted(x[k] for x in a)[len(a) // 2] if a else None
    mean = lambda a, k: sum(x[k] for x in a) / len(a) if a else None
    out = {"requests": len(cache), "deals": len(deals), "completed": len(comp), "not_completed": len(notc),
           "completion_rate": 100 * len(comp) / len(deals) if deals else None,
           "comp": {"ret_median": med(comp, "ret"), "ret_mean": mean(comp, "ret"), "days_median": med(comp, "days"), "ann_median": med(comp, "ann"),
                    "share_positive": 100 * sum(1 for x in comp if x["ret"] > 0) / len(comp) if comp else None},
           "notc": {"ret_median": med(notc, "ret"), "ret_mean": mean(notc, "ret")},
           "all": {"ret_mean": mean(deals, "ret"), "ret_median": med(deals, "ret"),
                   "share_positive": 100 * sum(1 for x in deals if x["ret"] > 0) / len(deals) if deals else None},
           "by_period": {p: {"n": len(a), "mean": mean(a, "ret"), "median": med(a, "ret")} for p, a in
                         (("2015–21", [x for x in deals if x["date"] < study.SPLIT]), ("2022后", [x for x in deals if x["date"] >= study.SPLIT]))},
           "deals_list": sorted(deals, key=lambda x: x["date"])}
    p = Path(os.environ.get("STUDY37_OUT", study.DATA / "study37_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "deals_list"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
