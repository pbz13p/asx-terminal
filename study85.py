"""Round 85: S&P/ASX index changes around the small-cap boundaries -- can we make money from the passive-fund flows?

Frank 2026-11-18. Like round 83 (merger arbitrage) this is an event-driven "parking lot" for idle cash, not the main line.

Rules fixed BEFORE looking at results (from S&P DJI's public methodology and commentary, checked 2026-11-18):
  * Rebalances take effect after the close on the third Friday of Mar / Jun / Sep / Dec (prior to the open the next
    Monday). Announcement: second Friday of the month (one week notice) until Jun 2021; first Friday (two weeks) since
    Sep 2021.
  * S&P/ASX 200: quarterly, rank buffer add <= 179th, delete >= 221st, by 3-month average float market cap (FMC).
  * S&P/ASX 300: SEMI-ANNUAL (Mar, Sep), add <= 274th, delete >= 326th. Small Ordinaries = ASX 300 minus ASX 100, so its
    bottom edge IS the ASX 300 edge (and its top edge is the ASX 100 edge, add <= 84th / delete >= 116th, quarterly).
  * All Ordinaries: top ~500 by 3-month average total market cap, annual (March) until 2025, semi-annual (Mar, Sep)
    from Sep 2025.
  * December and June therefore move only the 20/50/100/200 edges (plus corporate-action replacements), not the 300
    or All Ords edges.

Groups (all reported separately): ASX 200 edge, ASX 300 edge (= Small Ords bottom), All Ords edge; additions vs deletions.
Windows (excess return over the small-cap index, net of round 71's tiered costs, one round trip):
  W1 perfect-foresight pre-announcement: close 10 trading days before the announcement -> close on announcement day
     (announcements come after the close, so this is the upper bound for any prediction model);
  W2 announcement -> effective: close on announcement day -> close on the last day before it takes effect;
  W3 after: close on the last day before effective -> 20 trading days later (deletions often bounce here);
  W2+W3 and a reversed W3 are reported too.
Statistics: mean, median, hit rate, worst single trade, t clustered by rebalance date. In-sample 2015-2021, out-of-sample
2022 on. "Effective" = OOS excess > 5% a year after costs AND t > 2 AND >= 100 events; fewer events = "not reliable".
Capacity: 5% of the stock's median daily $ traded over the 60 days before entry, per stock per event.

Phase 1 (this file, first run): rebuild the lists. Each S&P DJI rebalance announcement is lodged on the ASX feed of every
company it affects, so it is already in our announcement cache (data/ann). One copy per announcement date is enough;
when the cached text is cut short, the full PDF is fetched once (politely, a few dozen files in total).
Phase 2: event study on the real lists (W1 = upper bound). Only if W1 clears the bar is the prediction model (phase 3)
worth building -- ranking by 3-month average market cap needs shares on issue, which EODHD (our plan) does not have.
"""
import gzip
import json
import math
import os
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import study

OUT = Path(os.environ.get("STUDY85_OUT", "out/study85_results.json"))
ANN = study.DATA / "ann"
HEAD = re.compile(r"(S&P|S&amp;P|Standard\s*&\s*Poor).{0,60}(Rebalanc|Indices|Index)|Quarterly Rebalance|All Ordinaries.{0,30}(Rebalanc|Review)", re.I)
MONTHS = {m: i + 1 for i, m in enumerate("January February March April May June July August September October November December".split())}
SECTION = re.compile(r"(S&P/ASX\s*(?:20|50|100|200|300)\b(?:\s*Index)?|S&P/ASX Small Ordinaries(?:\s*Index)?|All Ordinaries(?:\s*Index)?)"
                     r"[ \t]*[–\-—:][ \t]*([^\n]{0,90})", re.I)   # header line: "No change" / "Effective Prior to the Open on ..." / "March 18, 2016 After Market Close"
DATE = re.compile(r"([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})")
HEAD2 = re.compile(r"Rebalanc|Quarterly Review|Annual Review", re.I)
ACTION = re.compile(r"^\s*(Addition|Deletion|Removal|Add|Delete|Remove)\s+([A-Z0-9]{3,4})\b\s*(.*)$", re.M)


def norm_index(s):
    s = s.upper()
    for k in ("SMALL ORDINARIES", "ALL ORDINARIES", "300", "200", "100", "50", "20"):
        if k in s:
            return {"SMALL ORDINARIES": "SmallOrds", "ALL ORDINARIES": "AllOrds"}.get(k, "ASX" + k)
    return s


def parse(text):
    """Return (effective_date or None, {index: {"add": [...], "del": [...], "nochange": bool}})."""
    out, eff = {}, None
    marks = list(SECTION.finditer(text))
    for k, m in enumerate(marks):
        idx = norm_index(m.group(1))
        seg = text[m.end(): marks[k + 1].start() if k + 1 < len(marks) else len(text)]
        rec = out.setdefault(idx, {"add": [], "del": [], "nochange": False})
        line = m.group(2)
        if line.lower().startswith("no change"):
            rec["nochange"] = True
            continue
        dm = DATE.search(line)
        if dm and dm.group(1) in MONTHS and not eff:
            eff = (f"{dm.group(3)}-{MONTHS[dm.group(1)]:02d}-{int(dm.group(2)):02d}", "open" if "open" in line.lower() else "close")
        for a in ACTION.finditer(seg):
            side = "add" if a.group(1).lower().startswith("add") else "del"
            if a.group(2) not in rec[side]:
                rec[side].append(a.group(2))
    return eff, out


def collect():
    """Every rebalance announcement in the cache, one entry per announcement date (best text kept)."""
    by_date = {}
    files = sorted(ANN.glob("*.json.gz"))
    for f in files:
        if f.name == "universe.json":
            continue
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
        except Exception:
            continue
        for it in d.get("listed", []):
            if not HEAD.search(it.get("headline", "")):
                continue
            doc = d.get("docs", {}).get(it["ids"], {})
            text = doc.get("text", "")
            cur = by_date.get(it["date"])
            score = (bool(doc.get("full")), len(text))
            if cur is None or score > cur["score"]:
                by_date[it["date"]] = {"date": it["date"], "headline": it["headline"], "ids": it["ids"], "href": it.get("href", ""),
                                       "code": d.get("code", f.name[:-8]), "text": text, "full": bool(doc.get("full")), "score": score}
    print("[85] company files", len(files), "rebalance-like announcement dates", len(by_date), flush=True)
    return by_date


FLOOR = {"ASX200": 500_000, "ASX300": 100_000, "AllOrds": 20_000}   # $ a day; fixed before looking at results


def cost(p):   # round 71: 0.2% + one tick per side
    return 0.002 + ((0.001 if p < 0.10 else 0.005 if p < 2 else 0.01) / p if p > 0 else 1)


def series(sym):
    rows = study.load(sym)
    if not sym.endswith(".INDX") and rows:
        import study80
        rows, _ = study80.fix_consolidations([r + (r[6] * r[5],) for r in rows])   # unadjusted ASX consolidations
    return [r[0] for r in rows], [r[4] for r in rows], [r[6] for r in rows], [r[6] * r[5] for r in rows], [r[1] for r in rows]


def at(dates, day):   # index of the last trading row on or before day
    i = bisect_right(dates, day) - 1
    return i if i >= 0 else None


def stats(xs, days):
    """mean / median / hit / worst, and t clustered by rebalance date."""
    if not xs:
        return None
    a = np.array(xs)
    g = defaultdict(list)
    for x, d in zip(xs, days):
        g[d].append(x)
    m = np.array([np.mean(v) for v in g.values()])
    t = float(m.mean() / (m.std(ddof=1) / math.sqrt(len(m)))) if len(m) > 2 and m.std(ddof=1) > 0 else None
    per_year = defaultdict(float)
    for d, v in g.items():
        per_year[d[:4]] += float(np.mean(v))   # one equal-weight basket per rebalance, summed over the year
    return {"n": len(a), "rebalances": len(m), "mean_pct": round(100 * a.mean(), 2), "median_pct": round(100 * float(np.median(a)), 2),
            "hit_pct": round(100 * float((a > 0).mean()), 1), "worst_pct": round(100 * float(a.min()), 1),
            "best_pct": round(100 * float(a.max()), 1), "t_by_date": round(t, 2) if t is not None else None,
            "per_year_pct": round(100 * float(np.mean(list(per_year.values()))), 2), "reliable": len(a) >= 100}


def events(lists):
    cal, bc, _, _, bo = series("AXJO.INDX")
    bench_name = "AXJO.INDX"
    for sym in ("AXSO.INDX", "XSO.INDX"):
        d2, c2, _, _, o2 = series(sym)
        if len(d2) > 1000:
            cal, bc, bo, bench_name = d2, c2, o2, sym
            break
    ev = []
    dropped = Counter()
    for L in lists:
        if not L["effective"]:
            continue
        A = L["announced"]
        ia = at(cal, A)
        ie = at(cal, L["effective"]) if L["effective_kind"] == "close" else bisect_left(cal, L["effective"]) - 1
        if ia is None or ie is None or ie <= ia or ia < 10 or ie + 20 >= len(cal):
            continue
        dts = {"pre": cal[ia - 10], "ann": cal[ia], "eff": cal[ie], "post": cal[ie + 20]}
        for idx, rec in L["indices"].items():
            grp = {"ASX200": "ASX200", "ASX300": "ASX300", "SmallOrds": "ASX300", "AllOrds": "AllOrds"}.get(idx)
            if not grp:
                continue
            for side in ("add", "del"):
                for code in rec[side]:
                    d, c, raw, val, op = series(code + ".AU")
                    if len(d) < 30:
                        continue
                    ix = {k: at(d, v) for k, v in dts.items()}
                    if None in ix.values() or d[ix["pre"]] < cal[ia - 15]:   # must have traded around the window
                        continue
                    b = {k: bc[at(cal, v)] for k, v in dts.items()}
                    liq = float(np.median(val[max(0, ix["pre"] - 60):ix["pre"] + 1])) if ix["pre"] > 0 else 0
                    if liq < FLOOR[grp]:   # an index member trading this little is a re-used code (EODHD keeps the new owner)
                        dropped[grp] += 1
                        continue

                    def w(a0, a1):
                        r = c[ix[a1]] / c[ix[a0]] - 1
                        rn = (1 + r) * (1 - min(cost(raw[ix[a1]]), .9)) * (1 - min(cost(raw[ix[a0]]), .9)) - 1
                        return rn - (b[a1] / b[a0] - 1)
                    def w_next(a1):   # the list comes out after the close: buy at the NEXT day's open
                        i0 = ix["ann"] + 1
                        bi = at(cal, dts["ann"]) + 1
                        if i0 >= len(d) or i0 > ix[a1] or op[i0] <= 0:
                            return None
                        p0raw = raw[i0] * op[i0] / c[i0] if c[i0] else raw[i0]
                        r = c[ix[a1]] / op[i0] - 1
                        rn = (1 + r) * (1 - min(cost(raw[ix[a1]]), .9)) * (1 - min(cost(p0raw), .9)) - 1
                        return rn - (b[a1] / (bo[bi] or bc[bi]) - 1)
                    ev.append({"date": A, "eff": dts["eff"], "index": grp, "side": side, "code": code, "price": raw[ix["pre"]], "liq": round(liq),
                               "cap_5pct": round(0.05 * liq), "W1": w("pre", "ann"), "W2_close": w("ann", "eff"), "W2": w_next("eff"), "W3": w("eff", "post"),
                               "W23": w_next("post"), "W12": w("pre", "eff")})
    return ev, bench_name, dict(dropped)


def main():
    by_date = collect()
    fetched = 0
    lists = []
    for day in sorted(by_date):
        a = by_date[day]
        eff, idx = parse(a["text"])
        if (not idx or not a["full"]) and a["href"] and fetched < 120:   # cut short in the cache: fetch the whole PDF once
            try:
                import study67
                text, _, _ = study67.pdf_text({"href": a["href"]})
                fetched += 1
                eff2, idx2 = parse(text)
                if sum(len(v["add"]) + len(v["del"]) for v in idx2.values()) >= sum(len(v["add"]) + len(v["del"]) for v in idx.values()):
                    eff, idx, a["text"] = eff2 or eff, idx2, text
            except Exception as e:
                print("[85] fetch failed", day, type(e).__name__, flush=True)
        n = sum(len(v["add"]) + len(v["del"]) for v in idx.values())
        lists.append({"announced": day, "effective": eff[0] if eff else None, "effective_kind": eff[1] if eff else None, "headline": a["headline"][:120], "changes": n,
                      "indices": {k: v for k, v in idx.items()}, "sample": a["text"][:7000] if n == 0 and HEAD2.search(a["headline"]) else None})
    # coverage by rebalance quarter (Mar/Jun/Sep/Dec, 2015 on); rebalance announcements are in the rebalance month
    have = defaultdict(list)
    for L in lists:
        if L["changes"] and L["announced"][5:7] in ("02", "03", "05", "06", "08", "09", "11", "12"):
            q = L["announced"][:4] + "-" + {"02": "03", "03": "03", "05": "06", "06": "06", "08": "09", "09": "09", "11": "12", "12": "12"}[L["announced"][5:7]]
            have[q].append(L["announced"])
    want = [f"{y}-{m}" for y in range(2015, 2027) for m in ("03", "06", "09", "12") if f"{y}-{m}" <= "2026-09"]
    out = {"phase": 1, "announcement_dates_found": len(lists), "pdfs_fetched": fetched,
           "quarters_wanted": len(want), "quarters_found": sum(1 for q in want if q in have),
           "quarters_missing": [q for q in want if q not in have],
           "by_quarter": {q: have[q] for q in want if q in have},
           "lists": lists}
    # phase 2: event study on the real lists
    seen, uniq = set(), []
    for L in sorted(lists, key=lambda L: -L["changes"]):   # the same rebalance can be lodged on several dates (re-issues)
        k = (L["effective"], json.dumps(L["indices"], sort_keys=True))
        if L["effective"] and L["changes"] and k not in seen:
            seen.add(k)
            uniq.append(L)
    ev, bench, dropped = events(sorted(uniq, key=lambda L: L["announced"]))
    keep, seen2 = [], set()
    for e in ev:   # a rebalance re-issued a few days later lists the same stocks again: count each stock once (first notice)
        k = (e["eff"], e["index"], e["side"], e["code"])
        if k not in seen2:
            seen2.add(k)
            keep.append(e)
    ev = keep
    res = {"benchmark": bench, "events": len(ev), "dropped_reused_code_or_no_trading": dropped, "liquidity_floor": FLOOR}
    for grp in ("ASX200", "ASX300", "AllOrds"):
        for side in ("add", "del"):
            E = [e for e in ev if e["index"] == grp and e["side"] == side]
            for part, f in (("in_2015_2021", lambda e: e["date"] < "2022"), ("out_2022_on", lambda e: e["date"] >= "2022")):
                P = [e for e in E if f(e)]
                res[f"{grp}_{side}_{part}"] = {W: stats([e[W] for e in P if e[W] is not None], [e["date"] for e in P if e[W] is not None]) for W in ("W1", "W2", "W2_close", "W3", "W12", "W23")}
                res[f"{grp}_{side}_{part}"]["capacity_per_rebalance_median"] = round(float(np.median(
                    [sum(e["cap_5pct"] for e in P if e["date"] == d) for d in {e["date"] for e in P}]))) if P else None
    out["event_study"] = res
    out["events"] = ev
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[85] done: quarters found", out["quarters_found"], "of", len(want), flush=True)   # counts only; lists are encrypted


if __name__ == "__main__":
    main()
