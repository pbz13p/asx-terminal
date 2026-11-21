"""Daily lists from round 98 (reversal ladder), run after the daily price download (study.yml).

Frank 2026-11-21: put the two round-98 signals into the terminal and start the live record.
  底部放量突破 (R4): a "long decline" stock (new 500-session closing low while <= 50% of its 3-year high, within the
      last 250 sessions) closes above its 250-session high with 20-day traded value >= 2x the 250-day average.
      Round 98: 有效 -- t >= 3 in both 2008-18 and 2019+.
  底部弹簧 (R1): same setup, close >= 1.5x the setup low and above the close 20 sessions before the low day.
      Round 98: 一般 -- shown, recorded, not yet trusted.
  底部观察: stocks inside a setup episode with no signal yet (the waiting room) -- context only, not recorded.
Rules are exactly round 98's (same functions); a signal counts once per episode. Lists show signals from the last
5 sessions; the live record (out/track_au_ladder.json, append-only, kept on the results branch) logs each on its day.

Also writes a coverage note: how far back delisted stocks go in our price cache (Frank asked, 2026-11-21).
"""
import json
import os
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np

import study
import study98 as S

OUT = Path(os.environ.get("LADDER_OUT", "out/ladder_lists.json"))
RECENT = 5   # sessions


def scan(rows):
    d = [r[0] for r in rows]
    o = np.array([r[1] for r in rows], float); h = np.array([r[2] for r in rows], float)
    lo = np.array([r[3] for r in rows], float); c = np.array([r[4] for r in rows], float)
    v = np.array([r[5] for r in rows], float); raw = np.array([r[6] for r in rows], float)
    val = raw * v
    s20, s50, s200 = S.sma(c, 20), S.sma(c, 50), S.sma(c, 200)
    minc500, maxh750 = S.roll_min(c, 500), S.roll_max(h, 750)
    maxh250_prev = np.concatenate([[np.nan], S.roll_max(h, 250)[:-1]])
    v20, v250 = S.sma(val, 20), S.sma(val, 250)
    from numpy.lib.stride_tricks import sliding_window_view
    med20 = np.full(len(c), np.nan)
    med20[19:] = np.median(sliding_window_view(val, 20), axis=1)
    tradable = (raw >= 0.01) & (med20 >= 20_000)
    ep_start, ep_low, ep_pre, seen, ep_day = None, None, None, set(), None
    fired_at = {}
    n = len(c)
    for i in range(max(750, n - 520), n):           # 520 sessions back is enough to rebuild the current episode
        if c[i] <= minc500[i] and c[i] <= 0.5 * maxh750[i]:
            lowday = i - int(np.argmin(lo[max(0, i - 59):i + 1][::-1]))
            ep_start, ep_low, seen, ep_day = i, float(np.min(lo[max(0, i - 59):i + 1])), set(), d[lowday]
            ep_pre = c[max(0, lowday - 20)]
            fired_at = {}
        if ep_start is not None and i - ep_start > 250:
            ep_start = None
        if ep_start is None or not tradable[i]:
            continue
        if "R1" not in seen and c[i] >= 1.5 * ep_low and c[i] > ep_pre:
            seen.add("R1"); fired_at["R1"] = i
        if "R4" not in seen and maxh250_prev[i] == maxh250_prev[i] and c[i] > maxh250_prev[i] and v250[i] > 0 and v20[i] >= 2 * v250[i]:
            seen.add("R4"); fired_at["R4"] = i
        for s, ok in (("R2", s200[i] == s200[i] and c[i] > s200[i]),
                      ("R3", s200[i] == s200[i] and s20[i] > s50[i] > s200[i] and c[i] > s20[i])):
            if ok:
                seen.add(s)
    if ep_start is None:
        return None
    return {"fired": fired_at, "seen": sorted(seen), "low": ep_low, "low_day": ep_day, "dates": d, "raw": raw,
            "c": c, "med20": med20, "maxh750": maxh750, "v20": v20, "v250": v250}


def main():
    t0 = time.time()
    last_day = None
    ix = study.load("AXSO.INDX")
    ixd = {r[0]: r[4] for r in ix}
    if ix:
        last_day = ix[-1][0]
    cutoff = (date.fromisoformat(last_day) - timedelta(days=45)).isoformat() if last_day else "9999"
    r4, r1, watch = [], [], []
    recent_days = Counter()
    cov_first, cov_last, n_files, n_live = Counter(), Counter(), 0, 0
    for fp in sorted(study.CACHE.glob("*.AU.json")):
        rows = study.load(fp.name[:-5])
        if not rows:
            continue
        n_files += 1
        if rows[-1][0] >= cutoff:
            n_live += 1
            recent_days.update(r[0] for r in rows[-12:])
        else:                                       # stopped trading: count by year of first and last trade
            cov_last[rows[-1][0][:4]] += 1
            cov_first[rows[0][0][:4]] += 1
        if len(rows) < 800 or rows[-1][0] < cutoff:
            continue
        st = scan(rows)
        if not st:
            continue
        code = fp.name[:-8]
        dts, raw, c = st["dates"], st["raw"], st["c"]
        base = {"code": code, "price": round(float(raw[-1]), 4), "low": round(float(st["low"]), 4), "low_day": st["low_day"],
                "x_low": round(float(c[-1] / st["low"]), 2) if st["low"] else None,
                "off_high": round(100 * (float(c[-1] / st["maxh750"][-1]) - 1), 1),
                "val20": round(float(st["med20"][-1])), "vol_x": round(float(st["v20"][-1] / st["v250"][-1]), 1) if st["v250"][-1] else None,
                "also": [s for s in st["seen"] if s not in ("R1", "R4")]}
        n = len(dts)
        for sig, lst in (("R4", r4), ("R1", r1)):
            i = st["fired"].get(sig)
            if i is not None and i >= n - RECENT:
                lst.append(dict(base, day=dts[i], px=round(float(raw[i]), 4), since_pct=round(100 * (float(c[-1] / c[i]) - 1), 1)))
        if not st["fired"]:
            watch.append(base)
    mdays = sorted(d for d, k in recent_days.items() if k >= 0.5 * max(recent_days.values() or [1]))
    if mdays:   # the index file can lag; the market's real last sessions come from the stocks themselves
        last_day, keep = mdays[-1], set(mdays[-RECENT:])
        r4[:] = [r for r in r4 if r["day"] in keep]
        r1[:] = [r for r in r1 if r["day"] in keep]
    for lst in (r4, r1):
        lst.sort(key=lambda x: (x["day"], x["val20"]), reverse=True)
    watch.sort(key=lambda x: x["val20"], reverse=True)
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "last_day": last_day,
           "r4": r4, "r1": r1, "watch": watch[:200], "watch_n": len(watch),
           "note": "第 98 轮：长期下跌股（创两年收盘新低、离三年高点跌一半以上）之后，放量突破一年新高（R4，有效）或弹簧（R1，一般）。"
                   "信号出现后下一个开盘价买，回测里一年内翻倍的比例明显高于同样处境但还没信号的股票。",
           "coverage": {"files": n_files, "live": n_live, "stopped": n_files - n_live,
                        "stopped_by_last_year": dict(sorted(cov_last.items())), "stopped_by_first_year": dict(sorted(cov_first.items()))}}
    try:
        import tracklog
        bench = ix[-1][4] if ix else None
        for lst_id, lst in (("ladder_r4", r4), ("ladder_r1", r1)):
            by_day = {}
            for r in lst:
                by_day.setdefault(r["day"], []).append({"code": r["code"], "px": r["px"]})
            for d, items in sorted(by_day.items()):
                tracklog.record("au_ladder", lst_id, d, items, ixd.get(d, bench))

        def since_ret(code, day):
            rr = study.load(f"{code}.AU")
            return tracklog.adj_return([(r[0], r[4]) for r in rr if r[4]], day)
        out["track"] = tracklog.summarise("au_ladder", since_ret, bench, {"ladder_r4": "底部放量突破", "ladder_r1": "底部弹簧"})
    except Exception as e:
        print("[ladder] track record failed:", type(e).__name__, e)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[ladder] done in {time.time() - t0:.0f}s")   # counts stay inside the encrypted file


if __name__ == "__main__":
    main()
