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
                     r"\s*[–\-—:]\s*(No change|Effective[^\n]{0,40}?(?:on|of)\s+(?:[A-Z][a-z]+,?\s+)?([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4}))", re.I)
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
        if m.group(2).lower().startswith("no change"):
            rec["nochange"] = True
            continue
        if m.group(3) and m.group(3).capitalize() in MONTHS:
            eff = eff or f"{m.group(5)}-{MONTHS[m.group(3).capitalize()]:02d}-{int(m.group(4)):02d}"
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
        lists.append({"announced": day, "effective": eff, "headline": a["headline"][:120], "changes": n,
                      "indices": {k: v for k, v in idx.items()}, "sample": a["text"][:1500] if n == 0 else None})
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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[85] done: quarters found", out["quarters_found"], "of", len(want), flush=True)   # counts only; lists are encrypted


if __name__ == "__main__":
    main()
