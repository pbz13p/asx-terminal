"""Round 102: can round 99's "before-takeoff" signals of 100-baggers pick right-tail stocks in advance?

Frank 2026-11-25 (spec in his words, kept): free data only -- cached daily prices incl. delisted stocks + the private
announcement archive (data/ann); announcement headlines read by code, no API.

Rules fixed 2026-11-25 BEFORE any result was looked at (not to be changed afterwards):
  PRICES: every cached ASX stock incl. delisted; consolidations fixed (round 80) and glitches cleaned (round 99 v2 clean()).
    Universe stock-day (the "100-bagger starting state", round 99): raw close >= A$0.002, 20-day median traded >= A$1,000,
    adjusted close <= 20% of the highest close of the previous 756 sessions (<= -80% off the 3-year high).
    Every event below counts only on a universe day (the trading day on or before the headline date).
  EVENTS (headline + date only; buy at the next session's open after the headline date):
    E1 rescue raise: placement / entitlement / rights issue / capital raising / share purchase plan headline, and that day
       the close is within 1.2x of the lowest close of the previous 250 sessions.
    E2 board change: the second (or later) "Initial Director's Interest Notice" (Appendix 3X) within 60 days.
    E3 suspension -> reinstatement with a deal: a reinstatement headline within 30 days after a suspension headline, and an
       acquisition / new-project headline (acquire, option to acquire, farm-in, joint venture, merger, binding agreement)
       between the suspension and 5 days after reinstatement. Event day = reinstatement.
    E4 ASX price query / aware letter: first one in 365 days.
    E5 new substantial holder: "Becoming a substantial holder" (a new >= 5% holder; changes of existing holders are a
       different headline and excluded); at most one per stock per 30 days.
    C1 = E1 and E2 within 120 days of each other (event day = the later one); C2 = E3 and E1 within 120 days.
  OUTCOMES (entry = next session's open; if that open is > 3x or < 1/3 of the close, the close is used):
    x2_1y: any close within 252 sessions >= 2x entry; x10_3y: any close within 756 sessions >= 10x entry;
    r1y: close 252 sessions later / entry - 1 - costs, capped at +900%; dd1y: lowest close within 252 sessions / entry - 1.
    Costs per side (CLAUDE.md): round 71 (0.2% + one price tick) + round 84f impact for an A$5,000 order:
      20-day daily volatility x sqrt(5,000 / 20-day median traded), capped at 20%. Exit uses the exit day's price/tick.
    Stopped trading inside the window: last close (delisted to zero counts as a loss). Still trading but window not
    finished: left out of that outcome.
  CONTROL: for each event, the universe stock-days with the same entry day, same raw-price band (<0.5c, 0.5-1c, 1-2c,
    2-5c, 5-20c, >=20c) and same 20-day-traded band (<1k, 1-10k, 10-50k, 50-250k, >=250k), that have no event of that
    type within +-120 days. E1/C1/C2 controls must also be within 1.2x of their 1-year low (same state as E1).
    Event minus the mean of its control cell (cells with >= 5 controls). t = mean / se of monthly means of the
    differences (clustered by entry month).
  PERIODS: A = entries 2017-01-01 .. 2021-06-30, B = 2021-07-01 .. 2025-11-20 (only 2017 on: round 98 found delisted
    stocks are complete only from 2017; announcements before 2015 are sparse).
  SHUFFLE: for each event type, each event's date is moved to a random universe day of the same stock (same state filter),
    200 times; statistic = mean (x2_1y - control) over all events with a control. Real result must beat 95% of shuffles.
  VERDICT per event (primary measure x2_1y): 有效 = both periods higher than control with t >= 3 AND shuffle rank >= 95%
    AND >= 20 events every year 2017-2024; the first two met but < 20 a year = 线索; t 2-3 in both and rank >= 95% = 一般;
    otherwise 无效. Secondary (reported, not deciding): r1y, x10_3y, dd1y.
  EXTRAS: (1) catch rate -- round 99's clean 100-baggers with the low from 2013 (no one-day 3x jump in the rise, raw low
    >= 0.2c, tradable at the peak): did each event type fire between 365 days before the low and the takeoff day (first
    close >= 3x the low)? Plus false alarms = events per year. (2) Round 100 dispersion as a bonus only: event x2_1y when
    the latest top-20 table (annual report within 550 days before) shows top 20 <= 40%, vs > 40%.
  Trials registered: 7 events x 1 primary measure (x2_1y) = 7; secondary measures are descriptive.
"""
import gzip
import json
import math
import os
import re
import time
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import study

OUT = Path(os.environ.get("STUDY102_OUT", "out/study102_results.json"))
A0, SPLIT, END = "2017-01-01", "2021-07-01", "2025-11-20"
TYPES = ["E1", "E2", "E3", "E4", "E5", "C1", "C2"]
P_EDGES, L_EDGES = (0.005, 0.01, 0.02, 0.05, 0.20), (1e3, 1e4, 5e4, 2.5e5)
RE_RAISE = re.compile(r"placement|entitlement|rights issue|non-?renounceable|renounceable offer|capital raising|"
                      r"share purchase plan|\bspp\b", re.I)
RE_3X = re.compile(r"initial director'?s'? interest|appendix 3x", re.I)
RE_SUSP = re.compile(r"suspension from (official )?quotation|voluntary suspension|\bsuspension\b", re.I)
RE_REIN = re.compile(r"reinstatement", re.I)
RE_DEAL = re.compile(r"acqui|option to acquire|farm[- ]?in|joint venture|merger|binding (term|agreement|heads)", re.I)
RE_QUERY = re.compile(r"price query|aware letter|price and volume query|response to asx (price |aware )?query|asx (price )?query", re.I)
RE_SUB = re.compile(r"becoming a substantial (share)?holder", re.I)
RNG = np.random.default_rng(102)


def band(v, edges):
    for k, e in enumerate(edges):
        if v < e:
            return k
    return len(edges)


def tick(p):
    return 0.001 if p < 0.10 else 0.005 if p < 2 else 0.01


def d2(s):
    return date.fromisoformat(s)


def headlines(code):
    f = study.DATA / "ann" / f"{code}.json.gz"
    if not f.exists():
        return None, {}
    try:
        d = json.loads(gzip.decompress(f.read_bytes()))
    except Exception:
        return None, {}
    return sorted((it.get("date", "")[:10], it.get("headline", "") or "", it.get("ids")) for it in d.get("listed", [])
                  if it.get("date")), d.get("docs", {})


def find_events(H):
    """Raw event dates per type from headlines (state filters applied later on price days)."""
    ev = defaultdict(list)
    x3 = []
    last_q = None
    last_sub = None
    susp = []
    deals = [dd for dd, h, _ in H if RE_DEAL.search(h)]
    for dd, h, _ in H:
        if RE_RAISE.search(h):
            ev["E1"].append(dd)
        if RE_3X.search(h):
            x3 = [x for x in x3 if (d2(dd) - d2(x)).days <= 60] + [dd]
            if len(x3) >= 2:
                ev["E2"].append(dd)
        if RE_SUSP.search(h) and not RE_REIN.search(h):
            susp.append(dd)
        if RE_REIN.search(h):
            s0 = [s for s in susp if 0 <= (d2(dd) - d2(s)).days <= 30]
            if s0:
                lo, hi = s0[0], (d2(dd) + timedelta(days=5)).isoformat()
                if any(lo <= x <= hi for x in deals):
                    ev["E3"].append(dd)
        if RE_QUERY.search(h):
            if last_q is None or (d2(dd) - d2(last_q)).days > 365:
                ev["E4"].append(dd)
            last_q = dd
        if RE_SUB.search(h):
            if last_sub is None or (d2(dd) - d2(last_sub)).days > 30:
                ev["E5"].append(dd)
                last_sub = dd
    return {k: sorted(set(v)) for k, v in ev.items()}


def combine(a, b):
    out = []
    for x in a:
        for y in b:
            if abs((d2(x) - d2(y)).days) <= 120:
                out.append(max(x, y))
    return sorted(set(out))


def main():
    t0 = time.time()
    import study80 as S80
    from study99 import clean, fwd_max
    import study100 as S100
    cal = [r[0] for r in study.load("AXJO.INDX")]
    cal = [d for d in cal if d >= "2016-01-01"]
    cidx = {d: i for i, d in enumerate(cal)}
    ND = len(cal)
    # control aggregates: [type, day, cell, stat]; stats: n, x2, r1y, n_r, x10, n_x10, dd, n_dd
    AGG = np.zeros((len(TYPES), ND, 30, 8))
    stocks = []            # per stock compact arrays for the shuffle + events
    ev_rows = []           # (type, code, entry_day, cell, outcomes...)
    eps = []               # clean 100-baggers for the catch rate
    disp_info = {}
    files = sorted(study.CACHE.glob("*.AU.json"))
    for n, fp in enumerate(files):
        code = fp.name[:-8]
        rows = study.load(fp.name[:-5])
        if len(rows) < 300:
            continue
        rows, _ = S80.fix_consolidations([r + (r[6] * r[5],) for r in rows])
        d = [r[0] for r in rows]
        if d[-1] < A0:
            continue
        o = np.array([r[1] for r in rows], float)
        c = np.array([r[4] for r in rows], float); raw = np.array([r[6] for r in rows], float)
        vol = np.array([r[5] for r in rows], float); val = np.array([r[7] for r in rows], float)
        c = np.maximum(c, 1e-9)
        c, raw = clean(c, raw, vol)
        N = len(c)
        # rolling features
        import pandas as pd
        sc = pd.Series(c)
        hi3 = sc.rolling(757, min_periods=1).max().to_numpy()
        lo1 = sc.rolling(251, min_periods=1).min().to_numpy()
        med20 = pd.Series(val).rolling(20, min_periods=1).median().to_numpy()
        lr = np.diff(np.log(c), prepend=np.log(c[0]))
        sig20 = pd.Series(lr).rolling(20, min_periods=3).std().fillna(0.1).to_numpy()
        uni = (raw >= 0.002) & (med20 >= 1000) & (c <= 0.2 * hi3)
        low1 = c <= 1.2 * lo1
        alive = d[-1] >= END
        # outcomes for entry at session i+1 (signal on day i)
        out = {}
        for i in np.nonzero(uni)[0]:
            if d[i] < A0 or d[i] > END or i + 1 >= N:
                continue
            j = i + 1
            e = o[j] if 0 < o[j] and c[j] / 3 <= o[j] <= 3 * c[j] else c[j]
            imp = lambda k: min(0.2, sig20[k] * math.sqrt(5000 / max(med20[k], 1)))
            cin = 0.002 + tick(raw[j]) / max(raw[j], 1e-6) + imp(i)
            rec = {}
            k1 = j + 252
            if k1 < N or not alive:
                k = min(k1, N - 1)
                seg = c[j:k + 1]
                cout = 0.002 + tick(raw[k]) / max(raw[k], 1e-6) + imp(k)
                rec["x2"] = float((seg >= 2 * e).any())
                rec["r1y"] = min(9.0, float(c[k] / e - 1 - cin - cout))
                rec["dd"] = float(seg.min() / e - 1)
            k3 = j + 756
            if k3 < N or not alive:
                k = min(k3, N - 1)
                rec["x10"] = float((c[j:k + 1] >= 10 * e).any())
            if rec:
                rec["cell"] = band(raw[i], P_EDGES) * 5 + band(med20[i], L_EDGES)
                rec["low1"] = bool(low1[i])
                out[i] = rec
        # events
        H, docs = headlines(code)
        evd = find_events(H) if H else {}
        evd["C1"] = combine(evd.get("E1", []), evd.get("E2", []))
        evd["C2"] = combine(evd.get("E3", []), evd.get("E1", []))
        ev_idx = {}
        for t in TYPES:
            idx = set()
            for dd in evd.get(t, []):
                i = bisect_right(d, dd) - 1          # trading day on or before the headline
                if i < 0 or i not in out:
                    continue
                if t in ("E1", "C1", "C2") and not out[i]["low1"]:
                    continue
                idx.add(i)
            ev_idx[t] = sorted(idx)
        # control aggregates (exclude days with an event of that type within +-120 calendar days)
        for ti, t in enumerate(TYPES):
            edays = [d2(d[i]) for i in ev_idx[t]]
            for i, rec in out.items():
                if t in ("E1", "C1", "C2") and not rec["low1"]:
                    continue
                di = cidx.get(d[i + 1]) if i + 1 < N else None
                if di is None:
                    continue
                if edays:
                    dd = d2(d[i])
                    if any(abs((dd - x).days) <= 120 for x in edays):
                        continue
                a = AGG[ti, di, rec["cell"]]
                a[0] += 1
                if "x2" in rec:
                    a[1] += rec["x2"]; a[2] += rec["r1y"]; a[3] += 1; a[6] += rec["dd"]; a[7] += 1
                if "x10" in rec:
                    a[4] += rec["x10"]; a[5] += 1
        # events rows
        for t in TYPES:
            for i in ev_idx[t]:
                di = cidx.get(d[i + 1])
                if di is None:
                    continue
                ev_rows.append((t, code, d[i + 1], di, out[i]))
        # shuffle pools: per type, eligible days (same state) with their outcome
        pool = {"any": [(cidx.get(d[i + 1]), r) for i, r in out.items() if cidx.get(d[i + 1]) is not None],
                "low1": [(cidx.get(d[i + 1]), r) for i, r in out.items() if r["low1"] and cidx.get(d[i + 1]) is not None]}
        stocks.append((code, {t: len(ev_idx[t]) for t in TYPES}, pool))
        # dispersion: latest top-20 tables for this stock (annual reports in the archive + round 100 cache)
        if H:
            tabs = []
            extra = {}
            hf = S100.HOLD / f"{code}.json"
            if hf.exists():
                try:
                    extra = json.loads(hf.read_text())
                except Exception:
                    extra = {}
            for dd, h, ids in H:
                if re.search(r"annual report", h, re.I):
                    s = S100.parse_top20((docs.get(ids) or {}).get("text", "") or "")
                    top = s["top20"] if s else (extra.get(ids) or {}).get("top20")
                    if top:
                        tabs.append((dd, top))
            disp_info[code] = tabs
        # clean 100-baggers from 2013 (round 99 rules) for the catch rate
        r10 = fwd_max(c, 2520) / c
        q = r10 >= 100
        i = 0
        while i < N:
            if not q[i]:
                i += 1
                continue
            jj = i
            while jj + 1 < N and q[jj + 1]:
                jj += 1
            low = i + int(np.argmin(c[i:jj + 1]))
            endk = min(low + 2520, N - 1)
            pk = low + 1 + int(np.argmax(c[low + 1:endk + 1])) if low + 1 <= endk else low
            seg = c[low:pk + 1]
            jumps = int(np.sum(seg[1:] / np.maximum(seg[:-1], 1e-12) >= 3)) if len(seg) > 1 else 0
            tk = low + int(np.argmax(seg >= 3 * c[low]))
            around = val[max(0, pk - 10):pk + 11]
            if d[low] >= "2013-01-01" and jumps == 0 and raw[low] >= 0.002 and np.median(around) >= 1e4 and c[pk] / c[low] >= 100:
                eps.append({"code": code, "low": d[low], "takeoff": d[tk], "mult": round(float(c[pk] / c[low]), 1),
                            "events": {t: [x for x in evd.get(t, []) if (d2(d[low]) - timedelta(days=365)).isoformat() <= x <= d[tk]]
                                       for t in TYPES}})
            i = max(jj, pk) + 1
        if n % 500 == 0:
            print(f"[102] {n} files, {len(ev_rows)} events, {time.time() - t0:.0f}s", flush=True)

    # ---- control means per (type, day, cell)
    def ctl(ti, di, cell, k_sum, k_n):
        a = AGG[ti, di, cell]
        return a[k_sum] / a[k_n] if a[k_n] >= 5 else None

    def per_type(t, rows):
        ti = TYPES.index(t)
        res = {}
        for pn, lo, hi in (("A_2017_2021", A0, SPLIT), ("B_2021_2025", SPLIT, "9999")):
            R = [r for r in rows if lo <= r[2] < hi]
            diffs, dr, ev_x2, ct_x2, ev_r, ev_x10, ct_x10, ev_dd = [], [], [], [], [], [], [], []
            for _, _, day, di, o in R:
                if "x2" in o:
                    m = ctl(ti, di, o["cell"], 1, 3)
                    if m is not None:
                        diffs.append((day, o["x2"] - m)); ev_x2.append(o["x2"]); ct_x2.append(m)
                        mr = ctl(ti, di, o["cell"], 2, 3)
                        dr.append((day, o["r1y"] - mr)); ev_r.append(o["r1y"]); ev_dd.append(o["dd"])
                if "x10" in o:
                    m = ctl(ti, di, o["cell"], 4, 5)
                    if m is not None:
                        ev_x10.append(o["x10"]); ct_x10.append(m)
            res[pn] = {"events": len(R), "with_control": len(diffs),
                       "x2_1y_pct": round(100 * np.mean(ev_x2), 1) if ev_x2 else None,
                       "control_x2_1y_pct": round(100 * np.mean(ct_x2), 1) if ct_x2 else None,
                       "t_x2": tstat(diffs), "r1y_median_pct": round(100 * float(np.median(ev_r)), 1) if ev_r else None,
                       "r1y_mean_minus_control_pct": round(100 * float(np.mean([v for _, v in dr])), 1) if dr else None,
                       "t_r1y": tstat(dr), "x10_3y_pct": round(100 * np.mean(ev_x10), 2) if ev_x10 else None,
                       "control_x10_3y_pct": round(100 * np.mean(ct_x10), 2) if ct_x10 else None, "n_x10": len(ev_x10),
                       "dd1y_median_pct": round(100 * float(np.median(ev_dd)), 1) if ev_dd else None}
        return res

    def tstat(pairs):
        by = defaultdict(list)
        for dd, v in pairs:
            by[dd[:7]].append(v)
        m = np.array([np.mean(v) for v in by.values()])
        if len(m) < 6 or m.std(ddof=1) == 0:
            return None
        return round(float(m.mean() / (m.std(ddof=1) / math.sqrt(len(m)))), 2)

    by_type = defaultdict(list)
    for r in ev_rows:
        by_type[r[0]].append(r)
    results, verdict = {}, {}
    for t in TYPES:
        rows = by_type[t]
        res = per_type(t, rows)
        # per-year counts
        yc = defaultdict(int)
        for r in rows:
            yc[r[2][:4]] += 1
        res["per_year"] = dict(sorted(yc.items()))
        # shuffle
        ti = TYPES.index(t)
        real = [o["x2"] - ctl(ti, di, o["cell"], 1, 3) for _, _, _, di, o in rows
                if "x2" in o and ctl(ti, di, o["cell"], 1, 3) is not None]
        real_stat = float(np.mean(real)) if real else None
        counts = defaultdict(int)
        for _, code, _, _, _ in rows:
            counts[code] += 1
        pools = {code: pool for code, _, pool in stocks}
        sh = []
        if real:
            key = "low1" if t in ("E1", "C1", "C2") else "any"
            for _ in range(200):
                vals = []
                for code, k in counts.items():
                    P = pools.get(code, {}).get(key) or []
                    if not P:
                        continue
                    for s in RNG.integers(0, len(P), k):
                        di, o = P[s]
                        if "x2" in o:
                            m = ctl(ti, di, o["cell"], 1, 3)
                            if m is not None:
                                vals.append(o["x2"] - m)
                sh.append(float(np.mean(vals)) if vals else 0.0)
        rank = round(100 * float(np.mean([real_stat > s for s in sh])), 1) if sh and real_stat is not None else None
        res["shuffle"] = {"real_mean_x2_minus_control": round(real_stat, 4) if real_stat is not None else None,
                          "shuffle_p95": round(float(np.percentile(sh, 95)), 4) if sh else None, "rank_pct": rank}
        a, b = res["A_2017_2021"], res["B_2021_2025"]
        ta, tb = a["t_x2"], b["t_x2"]
        years_ok = all(yc.get(str(y), 0) >= 20 for y in range(2017, 2025))
        if ta is not None and tb is not None and ta >= 3 and tb >= 3 and (rank or 0) >= 95:
            v = "有效" if years_ok else "线索"
        elif ta is not None and tb is not None and ta >= 2 and tb >= 2 and (rank or 0) >= 95:
            v = "一般"
        else:
            v = "无效"
        verdict[t] = v
        # dispersion bonus
        dsp = {"top20_le_40": [], "top20_gt_40": []}
        for _, code, day, di, o in rows:
            if "x2" not in o:
                continue
            tabs = [x for x in disp_info.get(code, []) if x[0] <= day and (d2(day) - d2(x[0])).days <= 550]
            if not tabs:
                continue
            dsp["top20_le_40" if tabs[-1][1] <= 40 else "top20_gt_40"].append(o["x2"])
        res["dispersion_bonus"] = {k: {"n": len(v), "x2_1y_pct": round(100 * np.mean(v), 1) if v else None} for k, v in dsp.items()}
        results[t] = res
    # catch rate
    catch = {t: sum(1 for e in eps if e["events"][t]) for t in TYPES}
    out = {"round": 102, "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Rules fixed")[1][:6000],
           "results": results, "verdict": verdict,
           "catch": {"clean_100x_since_2013": len(eps), "caught_by": catch, "any": sum(1 for e in eps if any(e["events"][t] for t in TYPES)),
                     "episodes": eps},
           "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[102] done", out["seconds"], "s", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
