"""Round 101: "给自己发工资"型公司 -- do ASX explorers that spend a big share of their cash on payments to directors and
their associates (Appendix 5B item 6) do worse afterwards?

Frank 2026-11-25 (起因 CND / Mandrake / Winchester: the same West Perth team runs many shells; Mandrake's FY25 loss
about equalled the MD's pay). Data: every quarterly cash flow report (Appendix 5B, the 2019+ layout that has item 8.3
"Total relevant outgoings") in the private announcement archive (data/ann, round 68).

Rules fixed 2026-11-25 BEFORE any return was looked at:
  PARSE (code only, no AI; text normalised: non-breaking spaces -> spaces)
    * rp  = (6.1 + 6.2) / |8.3|   payments to related parties and their associates (items 1 + 2) over the quarter's
            total relevant outgoings (operating + capitalised exploration). Only quarters with 8.3 < 0 and |8.3| >= 50
            ($50k), rp capped at 1.
    * adm = |1.2(e) administration and corporate costs| / |8.3| (when parsed), capped at 1.
    * rp4 = mean rp of this and the company's 3 previous parsed quarters (needs >= 3 of 4) -- the "habit", not one quarter.
    * cash = 8.4, q87 = 8.7 kept for controls and the list.
    * One report per company per 70 days (the first); date = the announcement's release date.
  OUTCOMES: round 100's Px (first session after release, adjusted prices, consolidations fixed, round 71 costs,
    tradable rows only: raw price >= 1c, 20-day median traded >= A$5k). r60, r250, x2_250, x5_250, ex250 = r250 - AXSO.
  TESTS (quintiles within the release calendar year; Q5 = most paid to insiders vs Q1 = least; t with month clusters)
    T1 rp, T2 adm, T3 rp4. Measures: x2 rate, x5 rate, mean ex250 (capped +900%), median r250, mean r60.
    C1 controlled: OLS of x2_250 and of ex250 (capped) on the rp quintile (0-4) + log 20-day traded + past 250-session
       return (capped 0..10x) + 60-session volatility + log cash, month-clustered t for the rp term.
  Periods: A = 2019-07 to 2022-06, B = 2022-07 to 2025-11 (250-session outcomes need data).
  Verdict (CLAUDE.md, as round 100): T1/T2/T3 on x2 (and ex as a check) -- |t| >= 3 in BOTH periods, same sign,
    >= 100 rows in each group = 有效; |t| 2-3 = 一般; else 无效. Trials registered: 3 tests x 5 measures + 2 controlled = 17.
  Also for Frank ("普不普遍"): rp / adm distribution by size, share of quarters with rp >= 30%; CND, MAN, WEL, GGE,
    TON histories; the 2026 list of the highest-rp explorers (a watch-to-avoid list, not a signal unless verdict says so).
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
from study100 import Px, tdiff, _verdict

OUT = Path(os.environ.get("STUDY101_OUT", "out/study101_results.json"))
SPLIT, START, END = "2022-07-01", "2019-07-01", "2025-11-20"
NUM = r"(\(?[\d,]+(?:\.\d+)?\)?|-|–|—|nil)"
RE = {
    "rp1": re.compile(r"6\.1\s*Aggregate amount of payments to related parties[\s\S]{0,80}?included in\s+item\s+1\s*" + NUM, re.I),
    "rp2": re.compile(r"6\.2\s*Aggregate amount of payments to related parties[\s\S]{0,80}?included in\s+item\s+2\s*" + NUM, re.I),
    "out": re.compile(r"8\.3\s*Total relevant outgoings[\s\S]{0,60}?item\s+8\.2\)\s*" + NUM, re.I),
    "cash": re.compile(r"8\.4\s*Cash and cash equivalents at quarter end[\s\S]{0,30}?\(item\s+4\.6\)\s*" + NUM, re.I),
    "adm": re.compile(r"administration and corporate costs\s*" + NUM, re.I),
    "q87": re.compile(r"8\.7\s*Estimated quarters of funding available\s*(?:\([^)]*\))?\s*(>?\s*\d+(?:\.\d+)?)", re.I),
}
WATCH = ("CND", "MAN", "WEL", "GGE", "TON")


def num(s):
    if s is None:
        return None
    s = s.strip()
    if re.fullmatch(r"[-–—]|nil", s, re.I):
        return 0.0
    neg = "(" in s
    try:
        v = float(s.replace("(", "").replace(")", "").replace(",", "").replace(">", "").strip())
    except ValueError:
        return None
    return -v if neg else v


def parse5b(text):
    t = text.replace(" ", " ")
    if "Total relevant outgoings" not in t:
        return None
    r = {}
    for k, rx in RE.items():
        m = rx.search(t)
        r[k] = num(m.group(1)) if m else None
    if r["rp1"] is None or r["out"] is None or r["out"] >= 0 or abs(r["out"]) < 50:
        return None
    o = abs(r["out"])
    rec = {"rp": min(1.0, max(0.0, (abs(r["rp1"]) + abs(r["rp2"] or 0)) / o)), "spend": o, "cash": r["cash"], "q87": r["q87"]}
    if r["adm"] is not None:
        rec["adm"] = min(1.0, abs(r["adm"]) / o)
    return rec


def controls(px, day):
    i = bisect_right(px.d, day)
    if i < 260:
        return None
    c = px.c[:i]
    past = min(10.0, c[-1] / c[-251] if c[-251] > 0 else 1.0)
    lr = np.diff(np.log(np.maximum(c[-61:], 1e-6)))
    return {"past250": float(past), "vol60": float(lr.std() * math.sqrt(250))}


def ols_t(rows, ykey, cap=None):
    """x2/ex on rp quintile + controls; month-clustered t of the rp coefficient."""
    rows = [r for r in rows if r.get("ctl") and r.get("cash") and r["cash"] > 0 and "q" in r]
    if len(rows) < 200:
        return None
    y = np.array([min(float(r[ykey]), cap) if cap else float(r[ykey]) for r in rows])
    X = np.array([[1.0, r["q"], math.log(max(r["med20"], 1)), math.log(r["ctl"]["past250"] + 0.01), r["ctl"]["vol60"],
                   math.log(r["cash"])] for r in rows])
    try:
        XtXi = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    b = XtXi @ X.T @ y
    e = y - X @ b
    meat = np.zeros((X.shape[1], X.shape[1]))
    cl = defaultdict(list)
    for k, r in enumerate(rows):
        cl[r["date"][:7]].append(k)
    for idx in cl.values():
        s = X[idx].T @ e[idx]
        meat += np.outer(s, s)
    V = XtXi @ meat @ XtXi
    G = len(cl)
    V *= G / max(G - 1, 1)
    return {"coef": round(float(b[1]), 4), "t": round(float(b[1] / math.sqrt(V[1, 1])), 2) if V[1, 1] > 0 else None, "n": len(rows)}


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

    rows, seen5b, parsed, watch = [], 0, 0, defaultdict(list)
    files = sorted((study.DATA / "ann").glob("*.json.gz"))
    for k, f in enumerate(files):
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
        except Exception:
            continue
        code = d.get("code", f.name[:-8])
        docs = d.get("docs", {})
        own = []
        for ids, doc in sorted(docs.items(), key=lambda kv: kv[1].get("date", "")):
            day = (doc.get("date") or "")[:10]
            t = doc.get("text") or ""
            if day < START or "Appendix 5B" not in t or "Total relevant outgoings" not in t:
                continue
            seen5b += 1
            rec = parse5b(t)
            if not rec:
                continue
            parsed += 1
            if own and (np.datetime64(day) - np.datetime64(own[-1]["date"])).astype(int) < 70:
                continue
            rec.update({"code": code, "date": day})
            prev = [x["rp"] for x in own[-3:]]
            if len(prev) >= 2:
                rec["rp4"] = float(np.mean(prev + [rec["rp"]]))
            own.append(rec)
        if not own:
            continue
        px = Px(code)
        for rec in own:
            if code in WATCH:
                watch[code].append({x: rec.get(x) for x in ("date", "rp", "adm", "rp4", "spend", "cash", "q87")})
            if px.ok:
                o = px.outcome(rec["date"], (60, 250))
                if o:
                    rec.update(o)
                    if "r250" in o:
                        rec["ex250"] = o["r250"] - ax_ret(o["entry"], o["d1_250"])
                    rec["ctl"] = controls(px, rec["date"])
            rows.append(rec)
        if k % 500 == 0:
            print(f"[101] {k} files, {len(rows)} quarters, {time.time() - t0:.0f}s", flush=True)

    def q(v):
        v = [x for x in v if x is not None]
        return [round(float(np.quantile(v, p)), 3) for p in (0.25, 0.5, 0.75)] if v else None
    size = lambda r: "small(<50k/d)" if r.get("med20", 0) < 5e4 else "mid(50k-500k)" if r["med20"] < 5e5 else "large(>500k/d)"
    common = {"5b_reports_seen": seen5b, "parsed": parsed, "quarters": len(rows), "companies": len({r["code"] for r in rows}),
              "rp": q([r["rp"] for r in rows]), "adm": q([r.get("adm") for r in rows]),
              "rp_ge_30pct_share": round(100 * np.mean([r["rp"] >= 0.3 for r in rows]), 1),
              "rp_ge_50pct_share": round(100 * np.mean([r["rp"] >= 0.5 for r in rows]), 1)}
    for g in ("small(<50k/d)", "mid(50k-500k)", "large(>500k/d)"):
        rr = [r for r in rows if "med20" in r and size(r) == g]
        if rr:
            common[g] = {"n": len(rr), "rp": q([r["rp"] for r in rr]), "adm": q([r.get("adm") for r in rr]),
                         "rp_ge_30pct_share": round(100 * np.mean([r["rp"] >= 0.3 for r in rr]), 1)}

    withr = [r for r in rows if "r250" in r and START <= r["date"] <= END]

    def meas(lst):
        if not lst:
            return {"n": 0}
        r60 = [r["r60"] for r in lst if "r60" in r]
        return {"n": len(lst), "x2_pct": round(100 * np.mean([r["x2_250"] for r in lst]), 1),
                "x5_pct": round(100 * np.mean([r["x5_250"] for r in lst]), 1),
                "ex_mean_pct": round(100 * np.mean([min(r["ex250"], 9) for r in lst]), 1),
                "r_median_pct": round(100 * np.median([r["r250"] for r in lst]), 1),
                "r60_mean_pct": round(100 * np.mean([min(v, 9) for v in r60]), 1) if r60 else None,
                "var_median": round(float(np.median([r[VAR] for r in lst])), 3)}

    tests, groups_for_ols = {}, {}
    for var in ("rp", "adm", "rp4"):
        VAR = var  # noqa: N806  (read by meas)
        globals()["VAR"] = var
        res = {}
        for pn, pf in (("A_2019_2022", lambda d: d < SPLIT), ("B_2022_2025", lambda d: d >= SPLIT)):
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
                    g = sum(r[var] > c for c in cuts)
                    grp[g].append(r)
                    if var == "rp":
                        r["q"] = g
            res[pn] = {f"Q{g + 1}": meas(grp[g]) for g in range(5)}
            for m, key in (("x2", "x2_250"), ("x5", "x5_250"), ("ex", "ex250")):
                res[pn][f"t_{m}_Q5_vs_Q1"] = tdiff([(r["date"], float(min(r[key], 9))) for r in grp[4]],
                                                   [(r["date"], float(min(r[key], 9))) for r in grp[0]])
            res[pn]["t_r60_Q5_vs_Q1"] = tdiff([(r["date"], float(min(r["r60"], 9))) for r in grp[4] if "r60" in r],
                                              [(r["date"], float(min(r["r60"], 9))) for r in grp[0] if "r60" in r])
        tests[var] = res
    ctl = {}
    for pn, pf in (("A_2019_2022", lambda d: d < SPLIT), ("B_2022_2025", lambda d: d >= SPLIT)):
        lst = [r for r in withr if pf(r["date"])]
        ctl[pn] = {"x2": ols_t(lst, "x2_250"), "ex": ols_t(lst, "ex250", 9)}
    tests["controlled_rp"] = ctl
    verdict = {}
    for var in ("rp", "adm", "rp4"):
        a, b = tests[var]["A_2019_2022"], tests[var]["B_2022_2025"]
        verdict[var] = {"x2": _verdict(a.get("t_x2_Q5_vs_Q1"), b.get("t_x2_Q5_vs_Q1"), a["Q5"]["n"], b["Q5"]["n"]),
                        "ex": _verdict(a.get("t_ex_Q5_vs_Q1"), b.get("t_ex_Q5_vs_Q1"), a["Q5"]["n"], b["Q5"]["n"])}
    ca, cb = ctl["A_2019_2022"], ctl["B_2022_2025"]
    for m in ("x2", "ex"):
        if ca[m] and cb[m]:
            verdict[f"controlled_{m}"] = _verdict(ca[m]["t"], cb[m]["t"], ca[m]["n"], cb[m]["n"])
    latest = {}
    for r in rows:
        if r["date"] >= "2026-01-01" and (r["code"] not in latest or r["date"] > latest[r["code"]]["date"]):
            latest[r["code"]] = {x: (round(r[x], 3) if isinstance(r.get(x), float) else r.get(x))
                                 for x in ("code", "date", "rp", "rp4", "adm", "spend", "cash", "q87", "med20")}
    lat = sorted(latest.values(), key=lambda x: -(x.get("rp4") or x["rp"]))
    out = {"round": 101, "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Rules fixed")[1][:4500],
           "how_common": common, "tests": tests, "verdict": verdict, "watch": dict(watch),
           "latest_top": lat[:60], "latest_n": len(lat), "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[101] done", out["seconds"], "s", json.dumps(verdict, ensure_ascii=False), flush=True)


VAR = "rp"

if __name__ == "__main__":
    main()
