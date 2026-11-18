"""Round 80: ASX ten-baggers -- what did they look like BEFORE they took off, and can we pick them?

Frank's goal (CLAUDE.md): find the stocks most likely to go 5x / 10x, judge by expectancy, not hit rate.
Data: EODHD daily prices for every ASX stock incl. delisted (data/eod/*.AU.json). Announcements are not used here
(only 2021+ so far); this first pass is prices and trading activity only, so it can use 2008 onwards.

1. Snapshots: every stock at every month-end (2008 ->). Features use only data up to that day:
   price, liquidity (median daily $ traded), returns 1/3/6/12 months, distance from 3-year high and 1-year low,
   volatility, trend (above 200-day line, 50 > 200), turnover spike, age since listing, share of no-trade days.
2. Labels (future, for testing only): did it reach 10x (and 5x) of the snapshot close within 3 years, with the peak
   tradable (median $ traded >= $10k/day around the peak)? Forward 12-month return (delisted: last traded price).
3. Answers:
   a. base rates: how often a random stock-month becomes a 10x within 3 years;
   b. what share of ten-baggers started as thinly traded (< $1k/day) or new listings (< 6 months);
   c. lift by feature bucket (which conditions make a 10x more likely);
   d. walk-forward LightGBM (train on years ending >= 3 years before the test year, so labels never overlap):
      top-10% picks -- 10x rate vs base, 12-month return mean / median, hit rate, average win / loss, expectancy,
      biggest-winner share, and buildability (days to build a $7.5k position at <= 5% of daily turnover).
Output: out/study80_results.json (the workflow encrypts it; the public log only prints progress).
"""
import json
import math
import os
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

import study

OUT = Path(os.environ.get("STUDY80_OUT", "out/study80_results.json"))
START = "2008-01-01"
H3 = 756          # ~3 years of trading days
H12 = 250


def med(a):
    return float(np.median(a)) if len(a) else 0.0


def fix_consolidations(rows):
    """EODHD often leaves ASX share consolidations (e.g. 1-for-10) unadjusted: the price jumps 10x overnight while
    volume collapses by about the same ratio. Left in, every consolidation looks like a ten-bagger (first run of
    this round: 74% 'hit rate', +232% a year -- impossible). Rule: a one-day move of >= 3x (or <= 1/3) in the
    adjusted close with the next 5 days' median volume below 3/ratio of the previous 20 days' is a consolidation
    (or split); earlier prices are rescaled so the series is continuous. Returns (rows, n_fixed)."""
    rows = list(rows)
    n = 0
    for i in range(len(rows) - 1, 0, -1):
        a0, a1 = rows[i - 1][4], rows[i][4]
        if a0 <= 0:
            continue
        r = a1 / a0
        if 1 / 3 < r < 3:
            continue
        vb = med([x[5] for x in rows[max(0, i - 20):i]])
        va = med([x[5] for x in rows[i:i + 5]])
        if vb <= 0:
            continue
        ratio_v = va / vb
        # Only consolidations (price up >= 3x overnight with volume shrinking about as much). The old split branch
        # (price down >= 3x, volume not shrinking) matched real crashes -- volume usually jumps on bad news -- and
        # erased them (third run: 71% hit rate). Real splits are almost unheard of among ASX small caps.
        if r >= 3 and ratio_v < 1.0:   # 1.5/r caught only 64 in 3,776 stocks; ASX small caps consolidate by the hundreds
            n += 1
            for j in range(i):   # rescale everything before the jump (adjusted o/h/l/c; volume scaled the other way)
                d, o, h, l, c, v, raw, tv = rows[j]
                rows[j] = (d, o * r, h * r, l * r, c * r, v / r if r else v, raw, tv)
    return rows, n


def to_calendar(rows, cal):
    """EODHD has no row on days a stock did not trade. Counting rows as days made a thin stock's "12 months" really
    2-3 years and its "3 years" 6+ years (second run: 71% hit rate, +63% median -- impossible). Put every stock on the
    index's trading calendar: a no-trade day repeats the previous close with zero volume. Element 7 is the raw $ traded."""
    have = {r[0]: r for r in rows}
    lo, hi = rows[0][0], rows[-1][0]
    days = sorted(set(d for d in cal[bisect_left(cal, lo):] if d <= hi) | set(have))
    out, prev = [], None
    for d in days:
        r = have.get(d)
        if r is None:
            c, raw = prev[4], prev[6]
            out.append((d, c, c, c, c, 0, raw, 0.0))
        else:
            out.append(r)
        prev = out[-1]
    return out


def big_jumps(rows):
    """Dates of one-day moves >= 3x still left after the fix (real rallies or missed consolidations)."""
    return [rows[i][0] for i in range(1, len(rows)) if rows[i - 1][4] > 0 and rows[i][4] / rows[i - 1][4] >= 3]


def month_ends(dates):
    out, last = [], None
    for i, d in enumerate(dates):
        if last is not None and d[:7] != dates[last][:7]:
            out.append(last)
        last = i
    return out


def features(rows, i, first_day):
    c = np.array([r[4] for r in rows[max(0, i - 760):i + 1]])
    raw = rows[i][6]
    val = np.array([r[7] for r in rows[max(0, i - 250):i + 1]])
    v60 = val[-60:]
    p = c[-1]

    def ret(n):
        return p / c[-1 - n] - 1 if len(c) > n and c[-1 - n] > 0 else np.nan
    lr = np.diff(np.log(np.maximum(c[-61:], 1e-9))) if len(c) > 61 else np.array([np.nan])
    ma200 = c[-200:].mean() if len(c) >= 200 else np.nan
    ma50 = c[-50:].mean() if len(c) >= 50 else np.nan
    age = (date.fromisoformat(rows[i][0]) - date.fromisoformat(first_day)).days
    return {
        "price": raw, "liq": med(v60), "liq250": med(val),
        "r1": ret(21), "r3": ret(63), "r6": ret(126), "r12": ret(250),
        "off_high3y": p / c.max() - 1, "above_low1y": p / c[-250:].min() - 1 if len(c) >= 2 else np.nan,
        "vol": float(np.nanstd(lr) * math.sqrt(250)),
        "above200": float(p > ma200) if ma200 == ma200 else np.nan,
        "ma50_200": float(ma50 > ma200) if ma200 == ma200 else np.nan,
        "spike": (val[-20:].mean() / (val.mean() + 1e-9)) if len(val) > 40 else np.nan,
        "age_days": age, "zero_days": float((v60 == 0).mean()) if len(v60) else np.nan,
    }


def labels(rows, i):
    c0 = rows[i][4]
    fut = rows[i + 1:i + 1 + H3]
    if not fut:
        return None
    peak_k = max(range(len(fut)), key=lambda k: fut[k][4])
    mult = fut[peak_k][4] / c0
    around = [r[7] for r in fut[max(0, peak_k - 10):peak_k + 11]]
    tradable = med(around) >= 1e4 and rows[i][6] >= 0.005   # below half a cent one tick is already 20%: not a real 10x
    j = min(i + H12, len(rows) - 1)
    r12 = rows[j][4] / c0 - 1
    # round 71's real cost per side: 0.2% + one tick / price (tick 0.1c under 10c, 0.5c under $2, else 1c)
    cost = lambda p: 0.002 + ((0.001 if p < 0.10 else 0.005 if p < 2 else 0.01) / p if p > 0 else 1)
    r12n = (1 + r12) * (1 - min(cost(rows[j][6]), 0.9)) * (1 - min(cost(rows[i][6]), 0.9)) - 1
    complete = len(fut) >= H3 or rows[-1][0] < study_last  # delisted within 3y: history is complete
    return {"mult3y": mult, "x10": float(mult >= 10 and tradable), "x5": float(mult >= 5 and tradable),
            "r12": r12, "r12n": r12n, "complete": complete, "x10_any": float(mult >= 10)}


def main():
    global study_last
    files = sorted(study.CACHE.glob("*.AU.json"))
    cal = [r[0] for r in study.load("AXJO.INDX")]
    study_last = max(cal[-1], "2000")
    print("[80] stocks", len(files), "last day", study_last, flush=True)
    recs = []
    fixed = 0
    fixed_codes = set()
    n_jumps = 0
    for n, f in enumerate(files):
        rows = study.load(f.name[:-5])
        if len(rows) < 30:
            continue
        rows = [r + (r[6] * r[5],) for r in rows]   # raw $ traded, kept as-is through the consolidation fix
        rows, k = fix_consolidations(rows)
        jumps = big_jumps(rows)
        n_jumps += len(jumps)
        rows = to_calendar(rows, cal)
        fixed += k
        if k:
            fixed_codes.add(f.name[:-8])
        dates = [r[0] for r in rows]
        first = dates[0]
        code = f.name[:-8]
        for i in month_ends(dates):
            if dates[i] < START or rows[i][6] <= 0:
                continue
            x = features(rows, i, first)
            lab = labels(rows, i)
            if lab is None:
                continue
            j12 = dates[min(i + H12, len(dates) - 1)]
            lab["jump12"] = any(dates[i] < jd <= j12 for jd in jumps)
            recs.append({"code": code, "d": dates[i], **x, **lab})
        if n % 500 == 0:
            print("[80] scanned", n, "stocks,", len(recs), "stock-months", flush=True)
    keys = ["price", "liq", "liq250", "r1", "r3", "r6", "r12", "off_high3y", "above_low1y", "vol", "above200", "ma50_200",
            "spike", "age_days", "zero_days"]
    X = np.array([[r[k] for k in keys] for r in recs], dtype=float)
    y10 = np.array([r["x10"] for r in recs])
    y5 = np.array([r["x5"] for r in recs])
    r12 = np.array([r["r12n"] for r in recs])   # net of round-71 costs on both sides
    comp = np.array([r["complete"] for r in recs])
    year = np.array([int(r["d"][:4]) for r in recs])
    out = {"consolidations_fixed": fixed, "stock_months": len(recs), "stocks": len({r["code"] for r in recs}), "first": min(r["d"] for r in recs),
           "last": max(r["d"] for r in recs)}

    # a. base rates (complete 3-year windows only)
    m = comp
    out["base"] = {"x10_rate_pct": round(100 * y10[m].mean(), 3), "x5_rate_pct": round(100 * y5[m].mean(), 3),
                   "x10_untradable_extra_pct": round(100 * (np.array([r["x10_any"] for r in recs])[m].mean() - y10[m].mean()), 3),
                   "n": int(m.sum())}
    # ten-bagger "episodes": first month-end a stock qualifies, per stock per 3-year window
    first_hit = {}
    for r in recs:
        if r["x10"] and r["complete"]:
            k = r["code"]
            if k not in first_hit or r["d"] < first_hit[k]["d"]:
                first_hit[k] = r
    eps = list(first_hit.values())
    # b. where they started
    out["tenbaggers"] = {
        "stocks": len(eps),
        "start_thin_lt1k_pct": round(100 * np.mean([e["liq"] < 1e3 for e in eps]), 1) if eps else None,
        "start_lt10k_pct": round(100 * np.mean([e["liq"] < 1e4 for e in eps]), 1) if eps else None,
        "start_new_listing_lt6m_pct": round(100 * np.mean([e["age_days"] < 183 for e in eps]), 1) if eps else None,
        "start_price_lt5c_pct": round(100 * np.mean([e["price"] < 0.05 for e in eps]), 1) if eps else None,
        "start_off_high_median_pct": round(100 * med([e["off_high3y"] for e in eps]), 1) if eps else None,
        "days_to_build_7500_median": round(med([min(999, 7500 / max(0.05 * e["liq"], 1)) for e in eps]), 1) if eps else None,
        "by_year": dict(sorted(Counter(e["d"][:4] for e in eps).items())),
    }
    # c. lift by feature quintile (complete windows)
    lift = {}
    for j, k in enumerate(keys):
        col = X[m, j]
        ok = ~np.isnan(col)
        if ok.sum() < 1000:
            continue
        qs = np.nanquantile(col[ok], [0.2, 0.4, 0.6, 0.8])
        b = np.digitize(col[ok], qs)
        yy = y10[m][ok]
        base = yy.mean()
        lift[k] = {"edges": [float(q) for q in qs],
                   "x10_rate_pct": [round(100 * yy[b == g].mean(), 3) for g in range(5)],
                   "lift": [round(yy[b == g].mean() / base, 2) if base else None for g in range(5)]}
    out["lift_by_quintile"] = lift

    # d. walk-forward model
    try:
        import lightgbm as lgb
    except ImportError:
        lgb = None
    wf = []
    picks = []
    imp = []
    if lgb is not None:
        for ty in range(2014, max(year) + 1):
            tr = (year <= ty - 4) & comp     # labels need 3 years: a gap so no training label sees the test year
            te = year == ty
            if tr.sum() < 20000 or te.sum() < 500 or y10[tr].sum() < 30:
                continue
            mdl = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=200,
                                     subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1,
                                     scale_pos_weight=float((1 - y10[tr].mean()) / max(y10[tr].mean(), 1e-6)))
            mdl.fit(X[tr], y10[tr])
            imp.append(mdl.booster_.feature_importance("gain"))
            p = mdl.predict_proba(X[te])[:, 1]
            idx = np.where(te)[0]
            # top 10% each month
            by_m = defaultdict(list)
            for k2, ii in enumerate(idx):
                if recs[ii]["price"] >= 0.01 and recs[ii]["liq"] >= 2000:   # buyable: >= 1c, >= $2k a day
                    by_m[recs[ii]["d"][:7]].append((p[k2], ii))
            sel = []
            for mo, lst in by_m.items():
                lst.sort(reverse=True)
                sel += [ii for _, ii in lst[:max(1, len(lst) // 10)]]
            sel = np.array(sel)
            done = comp[sel]
            row = {"year": ty, "test_months": len(by_m), "picks": len(sel),
                   "x10_rate_top10_pct": round(100 * y10[sel][done].mean(), 3) if done.any() else None,
                   "x10_rate_all_pct": round(100 * y10[te & comp].mean(), 3) if (te & comp).any() else None,
                   "r12_median_top_pct": round(100 * np.nanmedian(r12[sel]), 1), "r12_median_all_pct": round(100 * np.nanmedian(r12[te]), 1)}
            wf.append(row)
            picks += list(sel)
        print("[80] walk-forward years", len(wf), flush=True)
    out["walk_forward"] = wf
    if imp:   # which features the model leans on (share of total gain)
        g = np.mean(imp, axis=0)
        out["importance"] = {k: round(float(v / g.sum()), 3) for k, v in sorted(zip(keys, g), key=lambda t: -t[1])}
    if picks:   # private diagnostics (the file is encrypted): what the picks look like, and the 40 biggest winners
        P0 = np.array(picks)
        out["pick_profile"] = {k: [round(float(np.nanmedian(X[P0, j])), 4), round(float(np.nanmedian(X[:, j])), 4)] for j, k in enumerate(keys)}
        top = sorted(P0, key=lambda i: -r12[i])[:40]
        out["top_picks"] = [{"code": recs[i]["code"], "d": recs[i]["d"], "price": recs[i]["price"], "liq": round(recs[i]["liq"]),
                             "r12n": round(float(r12[i]), 2), "mult3y": round(float(recs[i]["mult3y"]), 1)} for i in top]
    if picks:
        P = np.array(picks)
        rr = r12[P]
        wins, losses = rr[rr > 0], rr[rr <= 0]
        done = comp[P]
        allm = comp & (year >= min(w["year"] for w in wf))
        out["picks_summary"] = {
            "picks": len(P), "x10_rate_pct": round(100 * y10[P][done].mean(), 3) if done.any() else None,
            "x10_rate_all_pct": round(100 * y10[allm].mean(), 3),
            "x5_rate_pct": round(100 * y5[P][done].mean(), 3) if done.any() else None,
            "r12_mean_pct": round(100 * rr.mean(), 1), "r12_median_pct": round(100 * np.median(rr), 1),
            "r12_mean_capped_10x_pct": round(100 * np.minimum(rr, 9).mean(), 1),
            "all_buyable_r12_median_pct": round(100 * np.median(r12[allm & (X[:, 0] >= 0.01) & (X[:, 1] >= 2000)]), 1),
            "all_buyable_r12_mean_capped_pct": round(100 * np.minimum(r12[allm & (X[:, 0] >= 0.01) & (X[:, 1] >= 2000)], 9).mean(), 1),
            "hit_rate_pct": round(100 * (rr > 0).mean(), 1), "avg_win_pct": round(100 * wins.mean(), 1) if len(wins) else None,
            "avg_loss_pct": round(100 * losses.mean(), 1) if len(losses) else None,
            "expectancy_per_dollar": round(float(rr.mean()), 3),
            "top1pct_winners_share_of_gains": round(float(np.sort(rr)[-max(1, len(rr) // 100):].sum() / max(rr[rr > 0].sum(), 1e-9)), 3),
            "median_days_to_build_7500": round(med([min(999, 7500 / max(0.05 * recs[i]["liq"], 1)) for i in P]), 1),
            "unfixed_stocks_only": (lambda Q: {"picks": int(len(Q)), "r12_median_pct": round(100 * float(np.median(r12[Q])), 1) if len(Q) else None,
                "hit_rate_pct": round(100 * float((r12[Q] > 0).mean()), 1) if len(Q) else None})(np.array([i for i in P if recs[i]["code"] not in fixed_codes], dtype=int)),
            "fixed_stocks": len(fixed_codes), "jumps3x_left": n_jumps,
            "picks_with_3x_jump_pct": round(100 * float(np.mean([recs[i]["jump12"] for i in P])), 1),
            "no_jump_picks": (lambda Q: {"picks": int(len(Q)), "r12_median_pct": round(100 * float(np.median(r12[Q])), 1),
                "r12_mean_capped_pct": round(100 * float(np.minimum(r12[Q], 9).mean()), 1), "hit_rate_pct": round(100 * float((r12[Q] > 0).mean()), 1)})(
                np.array([i for i in P if not recs[i]["jump12"]], dtype=int)),
            "no_jump_all_buyable_median_pct": round(100 * float(np.median(r12[allm & (X[:, 0] >= 0.01) & (X[:, 1] >= 2000) & ~np.array([r["jump12"] for r in recs])])), 1),
            "calendar": "every stock on the index trading calendar (no-trade days = previous close, volume 0)", "note": "12-month returns net of round-71 costs both sides; picks only from stocks >= 1c trading >= $2k/day; no stops (round 82)",
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("[80] done:", len(recs), "stock-months; results in", OUT, flush=True)


study_last = ""

if __name__ == "__main__":
    main()
