"""Round 100: shareholder structure of ASX small caps -- how concentrated (top 20, top 1, top 2), is a director among the
biggest holders, and does the structure or its change tell us a stock is about to run (mainly up)?

Frank 2026-11-25 (起因 CND: top 20 hold ~42%, the largest holder ~5%).
Stage 1 probe (done 2026-11-25): 17,616 annual reports in the archive, 15,208 contain the Listing Rule 4.10 "twenty
largest holders" table (2,275 companies); ~7-10k substantial-holder notices a year (many are scanned images: use the
headline); 84,588 director-interest notices.

Rules for stage 2 fixed 2026-11-25 BEFORE any return was looked at:
  STRUCTURE (one row per annual report that has the table; date = the report's release date)
    * Parse the top-20 table: lines ending "<shares> <percent>" after the "twenty largest / top 20" heading; keep up to
      20 rows; top20 = the table's own "total" percent when it is between 1 and 100, else the sum of the rows.
      Rows must be in descending order (+-0.05 tolerance) and sum <= 100.5, else the report is skipped (unparsed).
    * top1, top2 = the two largest rows; nominee share = percent held by custodians / nominees (Citicorp, HSBC Custody,
      J P Morgan Nominees, BNP Paribas Nominees, National Nominees, UBS Nominees, Merrill Lynch, Morgan Stanley, Netwealth,
      Certane, Sandhurst Trustees...) inside the top 20; holders = number of holders from the distribution table when found.
    * Director among the biggest: the company's director names come from its own director-interest notices (Appendix
      3X/3Y/3Z "Name of Director"); a top-1 / top-2 row "belongs" to a director if the director's surname (>= 4 letters)
      appears in that row (covers family trusts / super funds named after them). Rough, so reported with its match rate.
    * Change: the same company's previous annual report (300-500 days earlier): d_top20, d_top1, d_holders (%).
  OUTCOMES (from the first session after the release date, adjusted prices, consolidations fixed as round 80)
    * r250 = close 250 sessions later / entry open - 1 - round 71 costs (0.2% + one tick per side); delisted: last price.
    * x2 = any close within 250 sessions >= 2x the entry; x5 likewise; ex = r250 minus the Small Ords (AXSO) return.
    * Tradable rows only: entry raw price >= 1c and 20-day median $ traded >= A$5k.
  TESTS (each: groups by the variable, compare top vs bottom group; t of the difference with month clusters)
    T1 top20 level      quintiles (within the same release year, so market-wide drift doesn't decide it)
    T2 top1 level       quintiles
    T3 nominee share    quintiles
    T4 director top 1/2 yes vs no
    T5 d_top20          quintiles (concentration rising vs falling over the year)
    T6 d_holders        quintiles (holder count rising vs falling)
    Measures: x2 rate, x5 rate, mean ex (capped at +900%), median r250.
  EVENTS (headline-based; first session after the date; tradable as above)
    E1 "Becoming a substantial holder" -- a new >= 5% holder; E2 "Ceasing to be a substantial holder";
    E3 a director on-market purchase (Appendix 3Y text: "on-market" with purchase/acquisition/bought in the nature of
       change, and no sale/disposal), E4 a director on-market sale.
    Each vs every other tradable stock-day of the same stocks (sampled 3% of days), horizons 20 / 60 / 250 sessions.
  Periods: 2015-2020 (in sample) and 2021 on (out of sample; 250-session outcomes need data, so releases up to 2025-11).
  Verdict (CLAUDE.md): |t| >= 3 in BOTH periods with the same sign and >= 100 rows/events in each = 有效; |t| 2-3 in
  both = 一般; otherwise 无效. Trials registered: 6 structure tests x 4 measures + 4 events x 3 horizons = 36.
  Also reported for Frank's question "普不普遍": distributions (median and quartiles) of top20, top1, top2, nominee
  share, holders, and the share of companies where a director is top 1 / top 2 -- by size (20-day median $ traded).
  CND's own rows are listed for reference.
"""
import gzip
import json
import math
import os
import re
import time
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import study

OUT = Path(os.environ.get("STUDY100_OUT", "out/study100_results.json"))
TOP = re.compile(r"(twenty|20) largest|top (twenty|20)|largest (twenty|20)", re.I)
ROW = re.compile(r"^(?:\s*\d{1,2}[.)]?\s+)?(.{3,140}?)\s+([\d,]{4,})\s+(\d{1,2}\.\d{1,4})\s*%?\s*$")
TOTAL = re.compile(r"total.{0,80}?([\d,]{5,})\s+(\d{1,3}\.\d{1,4})\s*%?", re.I)
NOMINEE = re.compile(r"citicorp|hsbc custody|j ?p morgan nominees|bnp paribas|national nominees|ubs nominees|merrill lynch|"
                     r"morgan stanley|netwealth|certane|sandhurst trustees|bond street custodians|nominees", re.I)
DIST = re.compile(r"(?m)^\s*(1|1,001|5,001|10,001|100,001)\s*(?:[-–]|to)?\s*(?:[\d,]+|and over|or more|over|\+)?\s+([\d,]+)\s+[\d, ]{3,}")
SPLIT = "2021-01-01"
END = "2025-11-20"


def num(s):
    return float(s.replace(",", ""))


def parse_top20(text):
    m = TOP.search(text)
    if not m:
        return None
    seg = text[m.end():m.end() + 6000]
    rows, total = [], None
    for line in seg.split("\n"):
        line = line.strip()
        if not line:
            continue
        t = TOTAL.search(line)
        if t and rows:
            v = float(t.group(2))
            if 1 <= v <= 100:
                total = v
            break
        r = ROW.match(line)
        if r:
            rows.append((r.group(1).strip(), num(r.group(2)), float(r.group(3))))
            if len(rows) >= 20:
                continue
        elif len(rows) >= 20:
            break
    rows = rows[:20]
    if len(rows) < 5:
        return None
    pct = [p for _, _, p in rows]
    if any(pct[k + 1] > pct[k] + 0.05 for k in range(len(pct) - 1)) or sum(pct) > 100.5:
        return None
    top20 = total if total is not None and total >= pct[0] else sum(pct)
    hm = None
    pre = text[max(0, m.start() - 5000):m.start()]
    cnt = [num(x.group(2)) for x in DIST.finditer(pre)]
    if 3 <= len(cnt) <= 8:
        hm = sum(cnt[-5:]) if len(cnt) >= 5 else sum(cnt)
    return {"rows": rows, "top20": top20, "top1": pct[0], "top2": pct[1] if len(pct) > 1 else 0.0,
            "nominee": sum(p for n, _, p in rows if NOMINEE.search(n)), "holders": hm if hm and 20 <= hm <= 1e6 else None}


def director_names(listed, docs):
    names = set()
    for it in listed:
        if not re.search(r"director'?s? interest|appendix 3[xyz]", it.get("headline", ""), re.I):
            continue
        t = (docs.get(it.get("ids")) or {}).get("text", "")
        for m in re.finditer(r"name of director\s*[:\-]?\s*([A-Za-z'\- .]{4,60})", t, re.I):
            n = m.group(1).strip().split("\n")[0]
            parts = [p for p in re.split(r"[\s.]+", n) if p]
            if parts:
                sur = parts[-1].strip("'-").upper()
                if len(sur) >= 4 and sur not in ("DIRECTOR", "LIMITED", "INTEREST", "NOTICE"):
                    names.add(sur)
    return names


def cost(p):
    return 0.002 + ((0.001 if p < 0.10 else 0.005 if p < 2 else 0.01) / p if p > 0 else 1)


class Px:
    def __init__(self, code):
        import study80 as S80
        rows = study.load(code + ".AU")
        self.ok = len(rows) >= 60
        if not self.ok:
            return
        rows, _ = S80.fix_consolidations([r + (r[6] * r[5],) for r in rows])
        self.d = [r[0] for r in rows]
        self.o = np.array([r[1] for r in rows]); self.c = np.array([r[4] for r in rows])
        self.raw = np.array([r[6] for r in rows]); self.val = np.array([r[7] for r in rows])

    def outcome(self, day, horizons=(250,)):
        i = bisect_right(self.d, day)
        if i < 21 or i >= len(self.d):
            return None
        raw = self.raw[i]
        if raw < 0.01 or np.median(self.val[i - 20:i]) < 5000:
            return None
        p = self.o[i]
        if not p > 0:
            return None
        out = {"entry": self.d[i], "med20": float(np.median(self.val[i - 20:i]))}
        for h in horizons:
            j = min(i + h, len(self.c) - 1)
            if j - i < h and self.d[-1] >= END:      # still trading, window not finished
                continue
            seg = self.c[i:j + 1]
            out[f"r{h}"] = float(self.c[j] / p - 1 - 2 * cost(raw))
            out[f"x2_{h}"] = bool((seg >= 2 * p).any())
            out[f"x5_{h}"] = bool((seg >= 5 * p).any())
            out[f"d1_{h}"] = self.d[j]
        return out


def tdiff(a, b):
    """a, b: lists of (date, value); month clusters."""
    def cl(lst):
        by = defaultdict(list)
        for d, v in lst:
            by[d[:7]].append(v)
        return np.array([np.mean(v) for v in by.values()])
    A, B = cl(a), cl(b)
    if len(A) < 3 or len(B) < 3:
        return None
    se = math.sqrt(A.var(ddof=1) / len(A) + B.var(ddof=1) / len(B))
    return round(float((A.mean() - B.mean()) / se), 2) if se > 0 else None


def main():
    t0 = time.time()
    try:
        study.download(["AXSO.INDX"])
    except Exception:
        pass
    ax = study.load("AXSO.INDX")
    axd, axc = [r[0] for r in ax], [r[4] for r in ax]

    def ax_ret(d0, d1):
        i, j = bisect_left(axd, d0), bisect_left(axd, d1)
        if i == 0 or i >= len(axc) or j >= len(axc):
            return 0.0
        return axc[j] / axc[i - 1] - 1

    reports, events = [], defaultdict(list)
    base_ev = []
    n_ar = n_parsed = 0
    dir_match = Counter()
    rng = np.random.default_rng(100)
    files = sorted((study.DATA / "ann").glob("*.json.gz"))
    for k, f in enumerate(files):
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
        except Exception:
            continue
        code = d.get("code", f.name[:-8])
        listed, docs = d.get("listed", []), d.get("docs", {})
        px = Px(code)
        if not px.ok:
            continue
        dirs = director_names(listed, docs)
        own = []
        for it in sorted(listed, key=lambda x: x.get("date", "")):
            h, day = it.get("headline", ""), it.get("date", "")
            if day < "2015-01-01" or day > END:
                continue
            if re.search(r"annual report", h, re.I):
                n_ar += 1
                t = (docs.get(it.get("ids")) or {}).get("text", "")
                s = parse_top20(t)
                if not s:
                    continue
                n_parsed += 1
                if own and own[-1]["date"] >= day[:4] + "-00":       # one report per calendar year (the first)
                    if own[-1]["date"][:4] == day[:4]:
                        continue
                top_names = [r[0].upper() for r in s["rows"][:2]]
                d1 = any(n in top_names[0] for n in dirs) if dirs else None
                d2 = any(n in top_names[1] for n in dirs) if dirs and len(top_names) > 1 else None
                dir_match["has_dirs" if dirs else "no_dirs"] += 1
                rec = {"code": code, "date": day, "top20": s["top20"], "top1": s["top1"], "top2": s["top2"],
                       "nominee": s["nominee"], "holders": s["holders"], "dir_top1": d1, "dir_top12": (d1 or d2) if dirs else None}
                prev = own[-1] if own else None
                if prev:
                    from datetime import date as _d
                    gap = (_d.fromisoformat(day) - _d.fromisoformat(prev["date"])).days
                    if 300 <= gap <= 500:
                        rec["d_top20"] = rec["top20"] - prev["top20"]
                        rec["d_top1"] = rec["top1"] - prev["top1"]
                        if rec["holders"] and prev.get("holders"):
                            rec["d_holders"] = rec["holders"] / prev["holders"] - 1
                own.append(rec)
                out = px.outcome(day)
                if out and "r250" in out:
                    rec.update(out)
                    rec["ex250"] = out["r250"] - ax_ret(out["entry"], out["d1_250"])
                reports.append(rec)
            else:
                ev = None
                if re.search(r"becoming a substantial", h, re.I):
                    ev = "E1"
                elif re.search(r"ceasing to be a substantial", h, re.I):
                    ev = "E2"
                elif re.search(r"director'?s? interest", h, re.I):
                    t = ((docs.get(it.get("ids")) or {}).get("text", "") or "").lower()
                    m = re.search(r"nature of change(.{0,400})", t, re.S)
                    seg = m.group(1) if m else ""
                    if re.search(r"on[- ]market", seg):
                        buy = re.search(r"purchase|acqui|bought", seg)
                        sell = re.search(r"\bsale\b|\bsold\b|dispos", seg)
                        ev = "E3" if buy and not sell else "E4" if sell and not buy else None
                if ev:
                    out = px.outcome(day, (20, 60, 250))
                    if out:
                        events[ev].append((day, out))
        # baseline days for the events: 3% of this stock's days 2015 on
        for i in range(21, len(px.d)):
            if px.d[i] >= "2015-01-01" and px.d[i] <= END and rng.random() < 0.03:
                out = px.outcome(px.d[i - 1], (20, 60, 250))
                if out:
                    base_ev.append((px.d[i - 1], out))
        if k % 500 == 0:
            print(f"[100] {k} files, {len(reports)} reports, {time.time() - t0:.0f}s", flush=True)

    def q(v):
        v = [x for x in v if x is not None]
        return [round(float(np.quantile(v, p)), 2) for p in (0.25, 0.5, 0.75)] if v else None
    tr = [r for r in reports]
    size = lambda r: "small(<50k/d)" if r.get("med20", 0) < 5e4 else "mid(50k-500k)" if r["med20"] < 5e5 else "large(>500k/d)"
    common = {"reports": len(tr), "annual_reports_seen": n_ar, "parsed": n_parsed, "director_names_found": dict(dir_match),
              "all": {"top20": q([r["top20"] for r in tr]), "top1": q([r["top1"] for r in tr]), "top2": q([r["top2"] for r in tr]),
                      "nominee": q([r["nominee"] for r in tr]), "holders": q([r["holders"] for r in tr]),
                      "dir_top1_pct": round(100 * np.mean([r["dir_top1"] for r in tr if r["dir_top1"] is not None]), 1),
                      "dir_top12_pct": round(100 * np.mean([r["dir_top12"] for r in tr if r["dir_top12"] is not None]), 1)}}
    for g in ("small(<50k/d)", "mid(50k-500k)", "large(>500k/d)"):
        rr = [r for r in tr if "med20" in r and size(r) == g]
        if rr:
            common[g] = {"n": len(rr), "top20": q([r["top20"] for r in rr]), "top1": q([r["top1"] for r in rr]),
                         "dir_top12_pct": round(100 * np.mean([r["dir_top12"] for r in rr if r["dir_top12"] is not None] or [0]), 1)}
    # tests
    withr = [r for r in reports if "r250" in r]

    def meas(lst):
        if not lst:
            return {"n": 0}
        return {"n": len(lst), "x2_pct": round(100 * np.mean([r["x2_250"] for r in lst]), 1),
                "x5_pct": round(100 * np.mean([r["x5_250"] for r in lst]), 1),
                "ex_mean_pct": round(100 * np.mean([min(r["ex250"], 9) for r in lst]), 1),
                "r_median_pct": round(100 * np.median([r["r250"] for r in lst]), 1)}

    def quint_test(var):
        res = {}
        for pn, pf in (("2015_2020", lambda d: d < SPLIT), ("2021_on", lambda d: d >= SPLIT)):
            lst = [r for r in withr if pf(r["date"]) and r.get(var) is not None]
            by_y = defaultdict(list)
            for r in lst:
                by_y[r["date"][:4]].append(r)
            grp = defaultdict(list)
            for y, rs in by_y.items():
                vals = sorted(r[var] for r in rs)
                if len(vals) < 25:
                    continue
                cuts = [vals[int(len(vals) * p)] for p in (0.2, 0.4, 0.6, 0.8)]
                for r in rs:
                    grp[sum(r[var] > c for c in cuts)].append(r)
            res[pn] = {f"Q{g + 1}": meas(grp[g]) for g in range(5)}
            for m, key in (("x2", "x2_250"), ("x5", "x5_250"), ("ex", "ex250")):
                res[pn][f"t_{m}_Q5_vs_Q1"] = tdiff([(r["date"], float(min(r[key], 9))) for r in grp[4]],
                                                   [(r["date"], float(min(r[key], 9))) for r in grp[0]])
        return res
    tests = {v: quint_test(v) for v in ("top20", "top1", "nominee", "d_top20", "d_holders")}
    t4 = {}
    for pn, pf in (("2015_2020", lambda d: d < SPLIT), ("2021_on", lambda d: d >= SPLIT)):
        yes = [r for r in withr if pf(r["date"]) and r.get("dir_top12") is True]
        no = [r for r in withr if pf(r["date"]) and r.get("dir_top12") is False]
        t4[pn] = {"director_top12": meas(yes), "not": meas(no)}
        for m, key in (("x2", "x2_250"), ("x5", "x5_250"), ("ex", "ex250")):
            t4[pn][f"t_{m}"] = tdiff([(r["date"], float(min(r[key], 9))) for r in yes], [(r["date"], float(min(r[key], 9))) for r in no])
    tests["dir_top12"] = t4
    ev_res = {}
    for e in ("E1", "E2", "E3", "E4"):
        ev_res[e] = {}
        for pn, pf in (("2015_2020", lambda d: d < SPLIT), ("2021_on", lambda d: d >= SPLIT)):
            a = [(d, o) for d, o in events[e] if pf(d)]
            b = [(d, o) for d, o in base_ev if pf(d)]
            row = {"n": len(a)}
            for h in (20, 60, 250):
                aa = [(d, o[f"r{h}"]) for d, o in a if f"r{h}" in o]
                bb = [(d, o[f"r{h}"]) for d, o in b if f"r{h}" in o]
                row[f"r{h}_mean_pct"] = round(100 * np.mean([min(v, 9) for _, v in aa]), 2) if aa else None
                row[f"base_r{h}_mean_pct"] = round(100 * np.mean([min(v, 9) for _, v in bb]), 2) if bb else None
                row[f"t{h}"] = tdiff([(d, min(v, 9)) for d, v in aa], [(d, min(v, 9)) for d, v in bb])
            aa = [o["x2_250"] for d, o in a if "x2_250" in o]
            row["x2_250_pct"] = round(100 * np.mean(aa), 1) if aa else None
            bb = [o["x2_250"] for d, o in b if "x2_250" in o]
            row["base_x2_250_pct"] = round(100 * np.mean(bb), 1) if bb else None
            ev_res[e][pn] = row
    verdict = {}
    for v, res in tests.items():
        if v == "dir_top12":
            ta, tb = res["2015_2020"].get("t_x2"), res["2021_on"].get("t_x2")
            na, nb = res["2015_2020"]["director_top12"]["n"], res["2021_on"]["director_top12"]["n"]
        else:
            ta, tb = res["2015_2020"].get("t_x2_Q5_vs_Q1"), res["2021_on"].get("t_x2_Q5_vs_Q1")
            na, nb = res["2015_2020"]["Q5"]["n"], res["2021_on"]["Q5"]["n"]
        verdict[v] = _verdict(ta, tb, na, nb)
    for e, res in ev_res.items():
        verdict[e] = _verdict(res["2015_2020"].get("t60"), res["2021_on"].get("t60"), res["2015_2020"]["n"], res["2021_on"]["n"])
    cnd = [{k: r.get(k) for k in ("date", "top20", "top1", "top2", "nominee", "holders", "dir_top12", "d_top20")} for r in reports if r["code"] == "CND"]
    out = {"round": 100, "stage": "study", "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Rules for stage 2")[1][:5500],
           "how_common": common, "tests": tests, "events": ev_res, "verdict": verdict, "cnd": cnd, "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[100] done", out["seconds"], "s", flush=True)


def _verdict(ta, tb, na, nb):
    if ta is None or tb is None or na < 100 or nb < 100:
        return "不可信"
    if abs(ta) >= 3 and abs(tb) >= 3 and ta * tb > 0:
        return "有效"
    if abs(ta) >= 2 and abs(tb) >= 2 and ta * tb > 0:
        return "一般"
    return "无效"


if __name__ == "__main__":
    main()
