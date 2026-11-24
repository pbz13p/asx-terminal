"""Round 99: every ASX stock that ever went 100x (within 1-5 years) -- what they looked like before, what made them go,
and which stocks look like that today.

Frank 2026-11-25: "去研究市面上那些翻过100倍的，不管是1年、2年、3年还是5年……总结他们为什么会翻……再反过来推出我们
的总结……再去套现在有没有这种股票". Free: cached prices + the announcement archive, no API.

Rules fixed 2026-11-25 before any result was seen:
  Prices: every cached ASX stock incl. delisted, consolidations fixed (round 80), on its own trading days.
  Episode: a low close L and a later close >= 100 x L within 2,520 sessions (10 years; Frank 2026-11-25: 1-10 years
    all count). One episode per run: the qualifying days are grouped, the low is the lowest close of the group, the peak
    the highest close within 2,520 sessions after the low; reported by how long it took (<=1, <=2, <=5, <=10 years).
    Also kept for context: 30-100x episodes ("近百倍").
  Data cleaning (v2, 2026-11-25, after the first run showed 611 of 701 "episodes" were data glitches: prices of
    0.0001 -- below the ASX minimum of 0.1c -- and one-day spikes that undo themselves): before anything else,
    (a) a raw price below A$0.001 is an error -> previous close; (b) a one-day move of >= 3x up or down that is mostly
    undone within 5 sessions (back within 1.5x of the day before) is a spike -> previous close; (c) a >= 3x up-move that
    holds but comes on volume below the 20-day median is an unadjusted consolidation -> earlier prices rescaled (round 80).
    A >= 3x day that holds on heavy volume is real news and stays. The same cleaned prices are used for the base rates.
  Sanity: an episode whose rise still contains a one-day jump >= 3x is marked (real news jump, kept);
    "tradable at the peak" = median $ traded >= A$10k/day in the 21 sessions around the peak (round 80 rule).
  Per episode: low day / raw price / 20-day median $ traded / share of no-trade days in the year before / distance from
    the 3-year high; takeoff = first close >= 3x the low (days after the low); peak day, multiple, years to peak;
    where it is now (last close / peak); sector (ASX company API); announcements from 120 sessions before the low to
    60 after takeoff (headline, date, price-sensitive flag) and keyword groups counted on the headlines:
      discovery (drill, assay, intercept, discovery, high-grade, mineralis), resource (JORC, resource, reserve),
      deal (acquisition, acquire, option to acquire, farm-in, joint venture, merger, binding), money (placement,
      entitlement, capital raising, share purchase plan, proposed issue), pivot (change of name / company name,
      reinstatement, recompliance, new business), commercial (offtake, contract, agreement, revenue, sales, order),
      approval (approval, permit, licence, granted, FDA, TGA), theme words (lithium, rare earth, uranium, gold, nickel,
      graphite, helium, hydrogen, battery, cannabis, crypto, bitcoin, AI, artificial intelligence, covid, vaccine).
    Coverage note: the archive has no announcements for 1,040 delisted companies (round 87 finding).
  Base rates ("lift"): every stock at every month-end 2008-2020: label = close reached >= 100x within 1,260 sessions
    (5 years); a second label = within 2,520 sessions (10 years) on month-ends 2008-2015 only. Live stocks without the
    full future window are left out; delisted ones count with what they had. Buckets at the snapshot: raw price (<0.5c, 0.5-1c, 1-2c, 2-5c, 5-20c, >=20c),
    20-day median $ traded (<1k, 1-10k, 10-50k, 50-250k, >=250k), distance from the 3-year high (> -50%, -50..-80%,
    -80..-95%, <= -95%). Lift = rate in bucket / overall rate. Two-way table price x distance.
  Today's screen (context, not a buy list): stocks whose current bucket combination had a 100x rate >= 3x the overall
    rate in 2008-2020, split into "还在底部" (within 2x of the 1-year low) and "已经起飞" (>= 3x off the 1-year low in
    the last 120 sessions -- the state past 100-baggers were in at takeoff). Each with its latest headlines.
"""
import gzip
import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import study

OUT = Path(os.environ.get("STUDY99_OUT", "out/study99_results.json"))
H5, H10 = 1260, 2520
KW = {
    "discovery": r"drill|assay|intercept|intersect|discover|high[- ]grade|mineralis|mineraliz",
    "resource": r"jorc|resource|reserve",
    "deal": r"acqui|option to acquire|farm[- ]in|joint venture|merger|binding",
    "money": r"placement|entitlement|capital raising|share purchase plan|proposed issue|rights issue",
    "pivot": r"change of (company )?name|reinstatement|re-?compliance|new business",
    "commercial": r"offtake|contract|agreement|revenue|sales|order",
    "approval": r"approv|permit|licen[cs]e|granted|\bfda\b|\btga\b",
    "theme": r"lithium|rare earth|uranium|\bgold\b|nickel|graphite|helium|hydrogen|battery|cannabis|crypto|bitcoin|\bai\b|artificial intelligence|covid|vaccine|vanadium|cobalt|copper",
}
KWC = {k: re.compile(v, re.I) for k, v in KW.items()}
THEMES = ["lithium", "rare earth", "uranium", "gold", "nickel", "graphite", "helium", "hydrogen", "battery", "cannabis",
          "crypto", "bitcoin", "ai", "artificial intelligence", "covid", "vaccine", "vanadium", "cobalt", "copper"]


def fwd_max(c, n):
    """max of c[i+1 .. i+n] for each i (NaN-safe enough for prices > 0)."""
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(c), np.nan)
    pad = np.concatenate([c[1:], np.full(n, -np.inf)])
    w = sliding_window_view(pad, n)
    out[:] = w.max(axis=1)[:len(c)]
    return out


def med(a):
    return float(np.median(a)) if len(a) else 0.0


def clean(c, raw, vol):
    """v2 cleaning (see header). Works on adjusted closes c and raw closes; returns new arrays."""
    c = c.copy(); raw = raw.copy()
    n = len(c)
    for i in range(1, n):
        if raw[i] < 0.001 or not c[i] > 0:
            c[i], raw[i] = c[i - 1], raw[i - 1]
    for i in range(1, n):
        r = c[i] / c[i - 1] if c[i - 1] > 0 else 1
        if r >= 3 or r <= 1 / 3:
            nxt = c[i + 1:i + 6]
            if len(nxt) and ((r >= 3 and nxt.min() <= 1.5 * c[i - 1]) or (r <= 1 / 3 and nxt.max() >= c[i - 1] / 1.5)):
                c[i], raw[i] = c[i - 1], raw[i - 1]
                continue
            if r >= 3:
                mv = np.median(vol[max(0, i - 20):i]) if i > 0 else 0
                if vol[i] < mv:
                    c[:i] *= r
    return c, raw


def anns(code):
    f = study.DATA / "ann" / f"{code}.json.gz"
    if not f.exists():
        return None
    try:
        d = json.loads(gzip.decompress(f.read_bytes()))
    except Exception:
        return None
    return sorted(((it.get("date", ""), it.get("headline", ""), bool(it.get("sensitive"))) for it in d.get("listed", [])))


def bucket(v, edges):
    for k, e in enumerate(edges):
        if v < e:
            return k
    return len(edges)


P_EDGES, L_EDGES, D_EDGES = (0.005, 0.01, 0.02, 0.05, 0.20), (1e3, 1e4, 5e4, 2.5e5), (-0.95, -0.80, -0.50)
P_NAMES = ["<0.5c", "0.5-1c", "1-2c", "2-5c", "5-20c", ">=20c"]
L_NAMES = ["<1k", "1-10k", "10-50k", "50-250k", ">=250k"]
D_NAMES = ["<=-95%", "-80..-95%", "-50..-80%", ">-50%"]


def main():
    t0 = time.time()
    import study80 as S80
    files = sorted(study.CACHE.glob("*.AU.json"))
    last_day = max((r[0] for r in study.load("AXJO.INDX")), default="2026-01-01")
    eps, near = [], []
    snap = Counter(); snap100 = Counter(); n_all = 0; n100 = 0
    s10, s10h = Counter(), Counter()
    today = []
    for n, fp in enumerate(files):
        code = fp.name[:-8]
        rows = study.load(fp.name[:-5])
        if len(rows) < 120:
            continue
        rows, _ = S80.fix_consolidations([r + (r[6] * r[5],) for r in rows])
        d = [r[0] for r in rows]
        c = np.array([r[4] for r in rows], float); raw = np.array([r[6] for r in rows], float)
        val = np.array([r[7] for r in rows], float); vol = np.array([r[5] for r in rows], float)
        if not (c > 0).all():
            c = np.maximum(c, 1e-9)
        c, raw = clean(c, raw, vol)
        ratio = fwd_max(c, H5) / c
        ratio10 = fwd_max(c, H10) / c
        # ---- episodes (100x) and near (30-100x)
        for lo_m, hi_m, sink in ((100, None, eps), (30, 100, near)):
            q = ratio10 >= lo_m
            i = 0
            while i < len(c):
                if not q[i]:
                    i += 1
                    continue
                j = i
                while j + 1 < len(c) and q[j + 1]:
                    j += 1
                low = i + int(np.argmin(c[i:j + 1]))
                end = min(low + H10, len(c) - 1)
                pk = low + 1 + int(np.argmax(c[low + 1:end + 1]))
                mult = c[pk] / c[low]
                if hi_m is not None and mult >= hi_m:
                    i = j + 1
                    continue
                seg = c[low:pk + 1]
                jumps = int(np.sum(seg[1:] / np.maximum(seg[:-1], 1e-12) >= 3))
                tk = low + int(np.argmax(c[low:pk + 1] >= 3 * c[low]))
                k100 = low + int(np.argmax(c[low:pk + 1] >= 100 * c[low])) if mult >= 100 else None
                around = val[max(0, pk - 10):pk + 11]
                yr = val[max(0, low - 250):low + 1]
                hi3 = c[max(0, low - 756):low + 1].max()
                sink.append({"code": code, "low_day": d[low], "low_raw": round(float(raw[low]), 5),
                             "low_med20": round(med(val[max(0, low - 19):low + 1])),
                             "zero_days_1y": round(float(np.mean(vol[max(0, low - 250):low + 1] == 0)), 3),
                             "off_high3y": round(float(c[low] / hi3 - 1), 3), "takeoff_day": d[tk], "days_to_takeoff": tk - low,
                             "peak_day": d[pk], "multiple": round(float(mult), 1), "years_to_peak": round((pk - low) / 252, 2),
                             "years_to_100x": round((k100 - low) / 252, 2) if k100 is not None else None,
                             "now_vs_peak": round(float(c[-1] / c[pk]), 3), "now_vs_low": round(float(c[-1] / c[low]), 1),
                             "listed_now": d[-1] >= last_day[:8] + "01" or d[-1] >= last_day,
                             "last_trade": d[-1], "tradable_peak": med(around) >= 1e4, "jumps3x": jumps,
                             "peak_med20": round(med(around)), "low_yr_med": round(med(yr))})
                i = max(j, pk) + 1          # one episode per run: the next one must start after this peak
        # ---- base rates at month-ends 2008-2020
        for k in range(len(d) - 1):
            if d[k][:7] == d[k + 1][:7] or d[k] < "2008-01-01" or d[k] > "2020-11-30":
                continue
            alive = d[-1] >= last_day[:7]
            if alive and k + H5 >= len(d):
                continue
            if raw[k] <= 0:
                continue
            m20 = med(val[max(0, k - 19):k + 1])
            hi3 = c[max(0, k - 756):k + 1].max()
            key = (bucket(raw[k], P_EDGES), bucket(m20, L_EDGES), bucket(c[k] / hi3 - 1, D_EDGES))
            hit = ratio[k] >= 100
            snap[key] += 1; snap100[key] += hit; n_all += 1; n100 += hit
            if d[k] <= "2015-11-30" and not (alive and k + H10 >= len(d)):
                s10[key] += 1; s10h[key] += ratio10[k] >= 100
        # ---- today's state
        if d[-1] >= last_day and len(c) > 260:
            k = len(c) - 1
            m20 = med(val[max(0, k - 19):k + 1])
            hi3 = c[max(0, k - 756):k + 1].max()
            lo1 = c[max(0, k - 250):k + 1].min()
            lo120 = c[max(0, k - 120):k + 1].min()
            today.append({"code": code, "key": (bucket(raw[k], P_EDGES), bucket(m20, L_EDGES), bucket(c[k] / hi3 - 1, D_EDGES)),
                          "raw": round(float(raw[k]), 4), "med20": round(m20), "off_high3y": round(float(c[k] / hi3 - 1), 3),
                          "vs_low1y": round(float(c[k] / lo1), 2), "up_from_120d_low": round(float(c[k] / lo120), 2)})
        if n % 500 == 0:
            print(f"[99] {n} files, {len(eps)} episodes, {time.time() - t0:.0f}s", flush=True)
    base = n100 / max(n_all, 1)
    # lift tables
    def tab(axis_names, pick):
        agg, agg100 = Counter(), Counter()
        for key, v in snap.items():
            agg[pick(key)] += v; agg100[pick(key)] += snap100[key]
        return {axis_names[g]: {"n": agg[g], "x100_per_10k": round(1e4 * agg100[g] / max(agg[g], 1), 2),
                                "lift": round((agg100[g] / max(agg[g], 1)) / base, 2) if base else None} for g in sorted(agg)}
    lift = {"overall_x100_per_10k": round(1e4 * base, 2), "stock_months": n_all, "x100_stock_months": n100,
            "by_price": tab(P_NAMES, lambda k: k[0]), "by_liquidity": tab(L_NAMES, lambda k: k[1]),
            "by_off_high": tab(D_NAMES, lambda k: k[2]),
            "ten_year": {"stock_months": sum(s10.values()), "x100_per_10k": round(1e4 * sum(s10h.values()) / max(sum(s10.values()), 1), 2),
                         "by_price": {P_NAMES[g]: round(1e4 * sum(s10h[k] for k in s10 if k[0] == g) / max(1, sum(s10[k] for k in s10 if k[0] == g)), 2)
                                      for g in range(len(P_NAMES))}},
            "price_x_off_high": {f"{P_NAMES[p]}|{D_NAMES[o]}": {"n": sum(v for k2, v in snap.items() if k2[0] == p and k2[2] == o),
                                                                 "x100_per_10k": round(1e4 * sum(snap100[k2] for k2 in snap if k2[0] == p and k2[2] == o) /
                                                                                       max(1, sum(v for k2, v in snap.items() if k2[0] == p and k2[2] == o)), 2)}
                                 for p in range(len(P_NAMES)) for o in range(len(D_NAMES))}}
    rate = {k: snap100[k] / snap[k] for k in snap if snap[k] >= 300}
    hot = {k for k, r in rate.items() if base and r >= 3 * base}
    # sectors + announcements for episodes
    try:
        import study94
        sec = study94.sectors(sorted({e["code"] for e in eps + near} | {t["code"] for t in today if t["key"] in hot}))
    except Exception:
        sec = {}
    for e in eps + near:
        e["sector"] = sec.get(e["code"])
        a = anns(e["code"])
        if a is None:
            e["ann_coverage"] = False
            continue
        e["ann_coverage"] = True
        # window: ~6 months before the low to ~3 months after takeoff (calendar approximation of the session rule)
        from datetime import date, timedelta
        w0 = (date.fromisoformat(e["low_day"]) - timedelta(days=180)).isoformat()
        w1 = (date.fromisoformat(e["takeoff_day"]) + timedelta(days=90)).isoformat()
        win = [x for x in a if w0 <= x[0] <= w1]
        e["n_ann_window"] = len(win)
        e["kw"] = {k: sum(1 for x in win if rx.search(x[1])) for k, rx in KWC.items()}
        e["themes"] = sorted({t for t in THEMES for x in win if re.search(r"\b" + re.escape(t) + r"\b", x[1], re.I)})
        sens = [x for x in win if x[2]]
        e["headlines"] = [[x[0], x[1][:110]] for x in (sens or win)][:25]
    # today's screen
    screen = {"bottom": [], "takeoff": []}
    for t in today:
        if t["key"] not in hot:
            continue
        t["bucket_x100_per_10k"] = round(1e4 * rate[t["key"]], 1)
        t["bucket"] = f"{P_NAMES[t['key'][0]]} | {L_NAMES[t['key'][1]]} | {D_NAMES[t['key'][2]]}"
        t["sector"] = sec.get(t["code"])
        a = anns(t["code"]) or []
        t["latest"] = [[x[0], x[1][:100]] for x in a[-5:]]
        t.pop("key")
        if t["up_from_120d_low"] >= 3:
            screen["takeoff"].append(t)
        elif t["vs_low1y"] <= 2:
            screen["bottom"].append(t)
    for k in screen:
        screen[k].sort(key=lambda t: -t["bucket_x100_per_10k"])
    # summary of episodes
    good = [e for e in eps if e["tradable_peak"]]
    def share(lst, f):
        return round(100 * sum(1 for e in lst if f(e)) / max(len(lst), 1), 1)
    cov = [e for e in good if e.get("ann_coverage")]
    summ = {"episodes_100x": len(eps), "clean_tradable": len(good), "with_real_3x_day": sum(1 for e in good if e["jumps3x"]),
            "distinct_stocks": len({e["code"] for e in good}), "near_30_100x": len(near),
            "by_low_year": dict(sorted(Counter(e["low_day"][:4] for e in good).items())),
            "years_to_peak_median": round(med([e["years_to_peak"] for e in good]), 2),
            "years_to_100x_median": round(med([e["years_to_100x"] for e in good if e["years_to_100x"] is not None]), 2),
            "within_1y_pct": share(good, lambda e: (e["years_to_100x"] or 99) <= 1), "within_2y_pct": share(good, lambda e: (e["years_to_100x"] or 99) <= 2),
            "within_5y_pct": share(good, lambda e: (e["years_to_100x"] or 99) <= 5),
            "low_below_1c_pct": share(good, lambda e: e["low_raw"] < 0.01), "low_below_0_2c_pct": share(good, lambda e: e["low_raw"] < 0.002),
            "low_med20_below_1k_pct": share(good, lambda e: e["low_med20"] < 1e3), "low_med20_below_10k_pct": share(good, lambda e: e["low_med20"] < 1e4),
            "off_high_median": round(med([e["off_high3y"] for e in good]), 3),
            "days_to_takeoff_median": round(med([e["days_to_takeoff"] for e in good])),
            "now_vs_peak_median": round(med([e["now_vs_peak"] for e in good]), 3),
            "still_listed_pct": share(good, lambda e: e["listed_now"]),
            "sectors": dict(Counter(e.get("sector") or "未知" for e in good).most_common()),
            "ann_coverage_pct": share(good, lambda e: e.get("ann_coverage")),
            "kw_any_pct": {k: share(cov, lambda e, k=k: e.get("kw", {}).get(k, 0) > 0) for k in KW},
            "themes": dict(Counter(t for e in cov for t in e.get("themes", [])).most_common())}
    out = {"round": 99, "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Rules fixed")[1][:5000], "summary": summ,
           "lift": lift, "hot_buckets": sorted(f"{P_NAMES[k[0]]} | {L_NAMES[k[1]]} | {D_NAMES[k[2]]}: {1e4 * rate[k]:.1f}/万" for k in hot),
           "screen": screen, "episodes": sorted(eps, key=lambda e: -e["multiple"]), "near": sorted(near, key=lambda e: -e["multiple"])[:300],
           "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[99] done", out["seconds"], "s", flush=True)


if __name__ == "__main__":
    main()
