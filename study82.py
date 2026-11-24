"""Round 82: on ASX small caps, which way of playing gives "A$100k -> A$10m in one year" a realistic chance?

Frank 2026-11-25. Deliverable: one table -- for each play, the chance after one year of 10x, 100x, losing half and
losing everything, plus how much money to put in. No API calls: only the cached daily prices and earlier results.

Rules fixed 2026-11-25 BEFORE any result was seen (nothing below may be changed after looking at the output):

DATA
  * Every ASX stock in the cache incl. delisted, consolidations fixed as in round 80 (study80.fix_consolidations), put on
    the ASX 200 trading calendar (no-trade day = previous close, zero volume; after the last trade the price stays at the
    last close -- a real zero would be worse, so delisting is if anything flattering).
  * Main period: one-year paths starting on the first session of each month 2017-01 .. 2025-11 (round 98: EODHD's
    delisted stocks are complete only from 2017). Paths starting 2009-01 .. 2016-12 are reported separately as an UPPER
    BOUND (failed companies missing). Paths whose year runs past the last data day (starts after about 2024-11) are
    not full years: they are left out of every probability and reported only as a count.
  * Fills: next session's open after the signal day. Costs per side: round 71 tiers (0.2% + one tick on the raw price) +
    round 84f square-root impact = k x daily volatility (60-session std of log returns) x sqrt(order $ / 20-session
    median $ traded), k = 0.5 / 1 / 2 (1 = main).
  * Capacity: an order may take at most 5% of that session's actual $ traded (ASX volume only -- Cboe missing, strict).
    Unfilled buys keep trying at the next opens for up to 5 sessions; unfilled sells keep selling at every open until
    done. Starting money A$100k / A$500k / A$2m. At the end of the year the account is also LIQUIDATED for real: every
    holding is sold from the next session on at <= 5% of each day's $ traded (up to 60 sessions; whatever is still unsold
    after that counts at zero). "Final multiple" = liquidated value / starting money (the mark-to-market multiple is
    reported too).

ENTRY SIGNALS (only ones already rated 有效 / 一般; nothing new)
  A  round 98 R4: bottom setup + close above the previous 250-session high with 20-day $ traded >= 2x the 250-day.
  B  round 98 R1: bottom setup + spring (close >= 1.5x the setup low and above the close 20 sessions before the low).
     A and B use round 98's exact setup (500-session closing low while <= 50% of the 3-year high; episode 250
     sessions; each signal once per episode; tradable = raw >= 1c and 20-session median $ traded >= A$20k).
  C  round 66 rocket (study94 S2): >= 50% below the 3-year high, 20-day median $ >= 2x the 1-year median, above the
     200-day line, raw >= 2c, 60-day median $ >= A$30k; first day only.
  D  round 80 model top 10%: walk-forward LightGBM exactly as study80 (trained on years <= test year - 4), each month the
     top 10% of buyable stocks (>= 1c, >= A$2k/day); signal day = that month-end. Out of sample only (test years 2014+).
  ALL  union of A-D; same-day ties by priority A > B > C > D, then higher 20-day median $ traded.
FILTERS (all plays incl. controls that use signals)
  F96  round 96 "不碰名单": no buying within 20 sessions after an announcement the AI scored as hype = 3, dilution
       risk = 3 or a capital raising. Scores exist mostly from 2025 (round 76 read order); coverage is reported -- before
       2025 this filter hardly bites.
  X85  a holding announced as deleted from the ASX 300 / Small Ords / All Ords is sold at the next open (round 85 lists).

PLAYS (each adds one piece to the one before; parameters N in {3, 5} positions, stop s in {15%, 20%})
  P1  concentrate: N equal slots (target = equity / N at the signal day); stop = entry x (1 - s); winners held to the
      end of the year. A slot freed by a LOSS (sold below cost) is refilled from the next signal with what is left;
      money from a winner (sold above cost, e.g. index deletion) is parked in cash.
  P2  + pyramid: buy half the slot target first; add 30% of the target when a close is >= 1.3x the first fill price
      and 20% when >= 2x (winners only, each once, from free cash); after each add the stop moves up to the average cost.
  P3  + recycle: money from winners also goes into the next signal (compounding).
  P4  + time stop: 10 sessions after entry, if the close is not above the first fill price, sell at the next open.
  P5  + 333 exits: when the close crosses below the 20-day average sell 1/3; when it later crosses below the 50-day
      average sell another 1/3; the last 1/3 goes when the close falls below the average cost (the s% stop still
      applies to the whole position until then).
CONTROLS
  RAND  at the start, N random tradable small caps (raw >= 1c, 20-day median $ A$20k-2m), held all year, no stops.
  X200  ASX 200 accumulation index (AXJOA), held all year (costs ignored).
  SIG   the first N signals of the year bought at full slot size, held to the end, no stops, no adds, no refills.

MEASURES (per play x signal x N x s x money x k)
  From the ~107 real 2017+ paths and from 5,000 block-bootstrap paths: a year is 4 blocks of 3 months; block j
  (months 3j+1..3j+3) is taken from the same months of a randomly drawn real path, so the account size at that stage
  of the year is roughly right; the liquidation haircut comes from the path that supplied the last block.
  Reported: median final multiple; P(>= 10x); P(>= 100x); P(<= 0.5x); P(<= 0.1x = "亏光"); longest run of losing
  trades; max drawdown (median and worst path); biggest single trade's share of all gains; share of entries that
  reached 10x within the year vs the RAND baseline's share.
  Position size: Kelly fraction f* maximising mean log(1 + f (X - 1)) over the bootstrap multiples X; suggestion =
  f*/4 (quarter Kelly) of total wealth; worst case = suggestion x (1 - 1st percentile of X).
VERDICTS
  * Trials registered: 5 signals x 5 plays x 2 N x 2 s = 100 configs (+ the 3 k levels and 3 money sizes as
    robustness, not selection). validate.py PBO on the 100 configs at A$100k, k = 1, using each path's log multiple as
    one period (overlapping years -- PBO is only indicative).
  * A play (signal x P) is "有效" only if at A$100k, k = 1, 2017+: at least 3 of its 4 (N, s) versions beat RAND on
    both the median multiple and P(>= 10x), and PBO < 0.5. One good parameter set alone = "巧合".
  * If P(>= 100x) < 1% for every play, the answer is "在澳股小盘做不到", with the reason judged from the numbers:
    signals too weak (even at k = 0 and unlimited capacity the mark-to-market P(100x) < 1%), costs eat it (P drops
    from k = 0.5 to 2), or capacity (P drops from A$100k to A$2m / the liquidated multiple is far below the marked one).
"""
import json
import math
import os
import time
from bisect import bisect_left
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

import study

START_MAIN, END_MAIN = "2017-01", "2025-11"
START_UB, END_UB = "2009-01", "2016-12"
SIGNALS = ("A", "B", "C", "D", "ALL")
PRIO = {"A": 0, "B": 1, "C": 2, "D": 3}
PLAYS = ("P1", "P2", "P3", "P4", "P5")
NS, STOPS = (3, 5), (0.15, 0.20)
MONEY = (100_000.0, 500_000.0, 2_000_000.0)
KS = (0.5, 1.0, 2.0)
ADV_CAP, BUY_TRIES, LIQ_DAYS, YEAR = 0.05, 5, 60, 252
N_BOOT = 5000
OUT = Path(os.environ.get("STUDY82_OUT", "out/study82_results.json"))


def tick(p):
    return 0.001 if p < 0.10 else 0.005 if p < 2 else 0.01


def side_cost(raw):
    return 0.002 + (tick(raw) / raw if raw > 0 else 1.0)


# ------------------------------------------------------------------------------------------------ data
G = {}          # filled in the parent before the worker pool forks


def sma(x, n):
    c = np.cumsum(np.insert(np.nan_to_num(x), 0, 0.0))
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rolling_median(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = np.median(sliding_window_view(x, n), axis=1)
    return out


def roll(x, n, fn):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = fn(sliding_window_view(x, n), axis=1)
    return out


def frame(rows, cal, cpos):
    """rows (fixed) -> arrays on the global calendar. Before listing: NaN."""
    T = len(cal)
    o = np.full(T, np.nan); c = np.full(T, np.nan); raw = np.full(T, np.nan); val = np.zeros(T); tr = np.zeros(T, bool)
    k0 = bisect_left(cal, rows[0][0])
    by = {r[0]: r for r in rows}
    last = None
    for k in range(k0, T):
        r = by.get(cal[k])
        if r:
            last = r
        if last is None:
            continue
        c[k] = last[4]; raw[k] = last[6]
        o[k] = r[1] if r else last[4]
        if r and r[5] > 0:
            val[k] = r[6] * r[5]; tr[k] = True
    lr = np.zeros(T); lr[1:] = np.log(np.maximum(c[1:], 1e-9) / np.maximum(c[:-1], 1e-9))
    lr[~tr] = np.nan
    sig = np.full(T, np.nan)
    if T > 60:
        from numpy.lib.stride_tricks import sliding_window_view
        w = sliding_window_view(lr, 60)
        cnt = np.sum(~np.isnan(w), axis=1)
        sd = np.nanstd(np.where(np.isnan(w), np.nan, w), axis=1)
        sd[cnt < 20] = np.nan
        sig[59:] = sd
    last_tr = int(np.flatnonzero(tr).max()) if tr.any() else -1
    return {"o": o.astype(np.float32), "c": c.astype(np.float32), "raw": raw.astype(np.float32), "val": val.astype(np.float32),
            "tr": tr, "med20": rolling_median(val, 20).astype(np.float32), "sig": sig.astype(np.float32),
            "ma20": sma(c, 20).astype(np.float32), "ma50": sma(c, 50).astype(np.float32), "first": k0, "last_tr": last_tr}


def r98_events(rows):
    """Round 98 R1 (spring) and R4 (breakout) on the stock's own trading rows; returns [(date, tag)]."""
    import study98 as S
    d = [r[0] for r in rows]
    if len(rows) < 800:
        return []
    h = np.array([r[2] for r in rows]); lo = np.array([r[3] for r in rows]); c = np.array([r[4] for r in rows])
    raw = np.array([r[6] for r in rows]); v = np.array([r[5] for r in rows]); val = raw * v
    minc500, maxh750 = S.roll_min(c, 500), S.roll_max(h, 750)
    maxh250_prev = np.concatenate([[np.nan], S.roll_max(h, 250)[:-1]])
    v20, v250 = S.sma(val, 20), S.sma(val, 250)
    med20 = rolling_median(val, 20)
    tradable = (raw >= 0.01) & (med20 >= 20_000)
    out = []
    ep_start, ep_low, ep_pre, seen = None, None, None, set()
    for i in range(750, len(c) - 1):
        if c[i] <= minc500[i] and c[i] <= 0.5 * maxh750[i]:
            lowday = i - int(np.argmin(lo[max(0, i - 59):i + 1][::-1]))
            ep_start, ep_low, seen = i, float(np.min(lo[max(0, i - 59):i + 1])), set()
            ep_pre = c[max(0, lowday - 20)]
        in_ep = ep_start is not None and i - ep_start <= 250
        if not in_ep:
            ep_start = None
            continue
        if not tradable[i]:
            continue
        if "R1" not in seen and c[i] >= 1.5 * ep_low and c[i] > ep_pre:
            seen.add("R1"); out.append((d[i], "B"))
        if "R4" not in seen and maxh250_prev[i] == maxh250_prev[i] and c[i] > maxh250_prev[i] and v250[i] > 0 and v20[i] >= 2 * v250[i]:
            seen.add("R4"); out.append((d[i], "A"))
    return out


def rocket_events(f, cal):
    """Round 66 rocket (study94 S2) on the calendar arrays: first day only."""
    c, raw, val = f["c"].astype(float), f["raw"].astype(float), f["val"].astype(float)
    ma200 = sma(c, 200)
    v20 = rolling_median(val, 20)
    v60 = rolling_median(val, 60)
    hi3 = roll(np.nan_to_num(c, nan=-1), 756, np.max)
    v250 = rolling_median(val, 250)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = (c / hi3 - 1 < -0.5) & (c > ma200) & (v250 > 0) & (v20 / v250 > 2) & (raw >= 0.02) & (v60 >= 3e4)
    ok &= np.arange(len(c)) >= f["first"] + 252
    idx = np.flatnonzero(ok[1:] & ~ok[:-1]) + 1
    return [(cal[i], "C") for i in idx]


def model80_events(cal):
    """Round 80 walk-forward top-10% picks, rebuilt exactly as study80 (out of sample, test years 2014+)."""
    import study80 as S80
    try:
        import lightgbm as lgb
    except ImportError:
        print("[82] lightgbm missing: signal D skipped", flush=True)
        return []
    S80.study_last = max(cal[-1], "2000")
    recs = []
    for f in sorted(study.CACHE.glob("*.AU.json")):
        rows = study.load(f.name[:-5])
        if len(rows) < 30:
            continue
        rows = [r + (r[6] * r[5],) for r in rows]
        rows, _ = S80.fix_consolidations(rows)
        rows = S80.to_calendar(rows, cal)
        dates = [r[0] for r in rows]
        first = dates[0]
        for i in S80.month_ends(dates):
            if dates[i] < S80.START or rows[i][6] <= 0:
                continue
            lab = S80.labels(rows, i)
            if lab is None:
                continue
            x = S80.features(rows, i, first)
            recs.append((f.name[:-8], dates[i], [x[k] for k in S80_KEYS], lab["x10"], lab["complete"], x["price"], x["liq"]))
    X = np.array([r[2] for r in recs], dtype=float)
    y = np.array([r[3] for r in recs]); comp = np.array([r[4] for r in recs]); year = np.array([int(r[1][:4]) for r in recs])
    out = []
    for ty in range(2014, max(year) + 1):
        trn = (year <= ty - 4) & comp
        te = year == ty
        if trn.sum() < 20000 or te.sum() < 500 or y[trn].sum() < 30:
            continue
        mdl = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=200, subsample=0.8,
                                 subsample_freq=1, colsample_bytree=0.8, verbose=-1,
                                 scale_pos_weight=float((1 - y[trn].mean()) / max(y[trn].mean(), 1e-6)))
        mdl.fit(X[trn], y[trn])
        idx = np.where(te)[0]
        p = mdl.predict_proba(X[idx])[:, 1]
        by_m = defaultdict(list)
        for k, ii in enumerate(idx):
            if recs[ii][5] >= 0.01 and recs[ii][6] >= 2000:
                by_m[recs[ii][1]].append((p[k], ii))
        for d, lst in by_m.items():
            lst.sort(reverse=True)
            out += [(recs[ii][1], "D", recs[ii][0]) for _, ii in lst[:max(1, len(lst) // 10)]]
    print("[82] round-80 picks", len(out), flush=True)
    return out


S80_KEYS = ["price", "liq", "liq250", "r1", "r3", "r6", "r12", "off_high3y", "above_low1y", "vol", "above200", "ma50_200",
            "spike", "age_days", "zero_days"]


def no_touch():
    """Round 96 不碰名单 from the cached AI scores: {code: [announcement dates]} for hype 3 / dilution 3 / raising."""
    out = defaultdict(list)
    n = 0
    for f in (study.DATA / "ai" / "s76").glob("*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        code, d = s.get("code"), s.get("date", "")
        if not code or not d:
            continue
        n += 1

        def num(x):
            try:
                return float(x)
            except Exception:
                return 0.0
        if num(s.get("hype")) >= 3 or num(s.get("dilution_risk")) >= 3 or s.get("type") == "capital_raising":
            out[code].append(d)
    return dict(out), n


def build():
    t0 = time.time()
    try:
        study.download(["AXJO.INDX", "AXJOA.INDX"])
    except Exception:
        pass
    cal = [r[0] for r in study.load("AXJO.INDX") if r[0] >= "2006-01-01"]
    cpos = {d: k for k, d in enumerate(cal)}
    import study80 as S80
    F, events = {}, defaultdict(list)
    for n, fp in enumerate(sorted(study.CACHE.glob("*.AU.json"))):
        code = fp.name[:-8]
        rows = study.load(fp.name[:-5])
        if len(rows) < 260:
            continue
        rows8, _ = S80.fix_consolidations([r + (r[6] * r[5],) for r in rows])
        rows = [r[:7] for r in rows8]
        if rows[-1][0] < cal[0]:
            continue
        f = frame(rows, cal, cpos)
        F[code] = f
        for d, tag in r98_events(rows) + rocket_events(f, cal):
            k = bisect_left(cal, d)
            if k < len(cal) and cal[k] == d:
                events[k].append((tag, code))
        if n % 500 == 0:
            print(f"[82] loaded {n} files, {len(F)} stocks, {time.time() - t0:.0f}s", flush=True)
    for d, tag, code in model80_events(cal):
        k = bisect_left(cal, d)
        if k < len(cal) and code in F:
            events[min(k, len(cal) - 1)].append((tag, code))
    nt, n_scored = no_touch()
    ntk = {c: sorted(bisect_left(cal, d) for d in ds) for c, ds in nt.items()}
    dele = defaultdict(set)
    try:
        import study94
        for d, codes in study94.index_deletions().items():
            k = bisect_left(cal, d)
            if k < len(cal):
                dele[k] |= set(codes)
    except Exception as e:
        print("[82] index deletions unavailable", type(e).__name__, flush=True)
    ax = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    axa = np.array([ax.get(d, np.nan) for d in cal], dtype=float)
    for k in range(1, len(axa)):
        if axa[k] != axa[k]:
            axa[k] = axa[k - 1]
    G.update(cal=cal, F=F, events=dict(events), ntk=ntk, n_scored=n_scored, dele=dict(dele), axa=axa,
             codes=sorted(F))
    print(f"[82] built: {len(F)} stocks, {sum(len(v) for v in events.values())} signal events, {time.time() - t0:.0f}s", flush=True)


# ------------------------------------------------------------------------------------------------ the account
def blocked(code, k):
    lst = G["ntk"].get(code)
    if not lst:
        return False
    j = bisect_left(lst, k - 20)
    return j < len(lst) and lst[j] <= k


def candidates(k, sig_set):
    evs = G["events"].get(k)
    if not evs:
        return []
    F = G["F"]
    seen, out = set(), []
    for tag, code in evs:
        if tag not in sig_set or code in seen:
            continue
        f = F[code]
        if not (f["raw"][k] >= 0.01) or not (f["med20"][k] >= 2000):
            continue
        seen.add(code)
        out.append((PRIO[tag], -float(f["med20"][k]), code, tag))
    out.sort()
    return [(c, t) for _, _, c, t in out]


def fill_cost(f, k, dollars, kimp):
    raw = float(f["raw"][k])
    sig = float(f["sig"][k]) if f["sig"][k] == f["sig"][k] else 0.05
    adv = max(float(f["med20"][k]), 1.0)
    return side_cost(raw) + kimp * sig * math.sqrt(max(dollars, 0) / adv)


def simulate(k0, k1, cfg, money, kimp, seed=0, mark_only=False):
    """One account from session k0 (signals from the close of k0) to session k1 (inclusive), then liquidated.
    cfg: dict(sig, play, n, stop). Returns a dict with month-end marks, trades and the liquidated value."""
    F, cal = G["F"], G["cal"]
    play, N, stop_p = cfg["play"], cfg["n"], cfg["stop"]
    sig_set = set("ABCD") if cfg["sig"] == "ALL" else {cfg["sig"]}
    lvl = int(play[1]) if play.startswith("P") else 0
    pyramid, recycle, tstop, three = lvl >= 2, lvl >= 3, lvl >= 4, lvl >= 5
    cash, parked = money, 0.0
    pos = {}                       # code -> dict
    buys = []                      # pending buys: [code, dollars_left, tries, tag, kind]
    trades = []
    marks = []
    eq_curve = []
    rng = np.random.default_rng(seed)
    used_sig = 0

    def equity(k):
        return cash + parked + sum(p["sh"] * float(F[c]["c"][k]) for c, p in pos.items())

    if cfg["sig"] == "RAND":       # control: N random small caps at the first open
        pool = [c for c in G["codes"] if F[c]["raw"][k0] >= 0.01 and 2e4 <= F[c]["med20"][k0] <= 2e6 and F[c]["tr"][k0]]
        for c in rng.choice(pool, size=min(N, len(pool)), replace=False):
            buys.append([c, money / N, 0, "R", "new"])
    for k in range(k0, k1 + 1):
        # ---- at the open of k: sells first, then buys
        if k > k0:
            for c, p in list(pos.items()):
                if p["sell"] <= 0:
                    continue
                f = F[c]
                if not f["tr"][k]:
                    continue
                px = float(f["o"][k])
                cap_sh = ADV_CAP * float(f["val"][k]) / max(float(f["raw"][k]), 1e-9) * (px / max(float(f["raw"][k]), 1e-9)) ** 0
                sh = min(p["sell"], p["sh"], ADV_CAP * float(f["val"][k]) / px if px > 0 else 0)
                if sh <= 0:
                    continue
                cost = fill_cost(f, k, sh * px, kimp)
                got = sh * px * (1 - min(cost, 0.95))
                p["sh"] -= sh; p["sell"] -= sh; p["proceeds"] += got
                cash += got
                if p["sh"] <= 1e-9:
                    pnl = p["proceeds"] - p["spent"]
                    trades.append({"code": c, "in": cal[p["k_in"]], "out": cal[k], "pnl": pnl, "spent": p["spent"],
                                   "peak": p["peak"] / p["px0"], "tag": p["tag"]})
                    if pnl > 0 and not recycle:
                        cash -= p["proceeds"]; parked += p["proceeds"]
                    del pos[c]
            nb = []
            for b in buys:
                c, left, tries, tag, kind = b
                f = F[c]
                if c in pos and kind == "new":
                    continue
                if not f["tr"][k] or not (f["o"][k] > 0):
                    if tries + 1 < BUY_TRIES:
                        nb.append([c, left, tries + 1, tag, kind])
                    continue
                px = float(f["o"][k])
                dollars = min(left, cash, ADV_CAP * float(f["val"][k]))
                if dollars >= 50:
                    cost = fill_cost(f, k, dollars, kimp)
                    sh = dollars * (1 - min(cost, 0.95)) / px
                    cash -= dollars
                    if c not in pos:
                        pos[c] = {"sh": 0.0, "spent": 0.0, "proceeds": 0.0, "px0": px, "k_in": k, "stop": px * (1 - stop_p),
                                  "adds": 0, "sell": 0.0, "tag": tag, "peak": px, "t1": False, "t2": False, "above20": False,
                                  "above50": False, "target": b[1] if kind == "new" else 0}
                    p = pos[c]
                    p["sh"] += sh; p["spent"] += dollars
                    if kind == "add":
                        p["stop"] = max(p["stop"], p["spent"] / max(p["sh"], 1e-12))
                    left -= dollars
                if left >= 50 and tries + 1 < BUY_TRIES and cash >= 50:
                    nb.append([c, left, tries + 1, tag, kind])
            buys = nb
        # ---- at the close of k
        dk = G["dele"].get(k, ())
        for c, p in pos.items():
            f = F[c]
            cl = float(f["c"][k])
            p["peak"] = max(p["peak"], cl)
            if p["sell"] > 0 or cfg["sig"] in ("RAND", "SIGHOLD"):
                continue
            avg = p["spent"] / max(p["sh"], 1e-12) if p["sh"] > 0 else p["px0"]
            if c in dk:
                p["sell"] = p["sh"]; continue
            if cl <= p["stop"]:
                p["sell"] = p["sh"]; continue
            if tstop and k - p["k_in"] == 10 and cl <= p["px0"]:
                p["sell"] = p["sh"]; continue
            if three:
                m20, m50 = float(f["ma20"][k]), float(f["ma50"][k])
                if not p["t1"]:
                    if cl >= m20:
                        p["above20"] = True
                    elif p["above20"]:
                        p["t1"] = True; p["sell"] = p["sh"] / 3
                        continue
                elif not p["t2"]:
                    if cl >= m50:
                        p["above50"] = True
                    elif p["above50"]:
                        p["t2"] = True; p["sell"] = p["sh"] / 2
                        continue
                else:
                    if cl < avg:
                        p["sell"] = p["sh"]; continue
            if pyramid and p["adds"] < 2:
                lvl_px = (1.3, 2.0)[p["adds"]]
                if cl >= lvl_px * p["px0"]:
                    frac = (0.3, 0.2)[p["adds"]]
                    p["adds"] += 1
                    buys.append([c, frac * p["target"] / 0.5, 0, p["tag"], "add"])
        # new entries
        if cfg["sig"] not in ("RAND",) and k < k1:
            free_slots = N - len(pos) - sum(1 for b in buys if b[4] == "new")
            if cfg["sig"] == "SIGHOLD" and used_sig >= N:
                free_slots = 0
            if free_slots > 0 and cash > 100:
                cands = candidates(k, set("ABCD") if cfg.get("base") == "ALL" else {cfg.get("base", cfg["sig"])})
                eq = equity(k)
                for c, tag in cands:
                    if free_slots <= 0 or cash < 100:
                        break
                    if c in pos or any(b[0] == c for b in buys) or blocked(c, k):
                        continue
                    tgt = eq / N
                    first = tgt * (0.5 if pyramid else 1.0)
                    buys.append([c, min(first, cash), 0, tag, "new"])
                    free_slots -= 1
                    used_sig += 1
        e = equity(k)
        eq_curve.append(e)
        if k == k1 or cal[k][:7] != cal[min(k + 1, len(cal) - 1)][:7]:
            marks.append(e)
    # pyramid target: stored as the first buy amount; restore the full slot target for adds
    mark_end = eq_curve[-1]
    # ---- liquidation after the year
    liq = cash + parked
    for c, p in pos.items():
        f = F[c]
        left = p["sh"]
        last_k = k1
        for k in range(k1 + 1, min(k1 + 1 + LIQ_DAYS, len(cal))):
            last_k = k
            if left <= 0:
                break
            if not f["tr"][k]:
                continue
            px = float(f["o"][k])
            sh = min(left, ADV_CAP * float(f["val"][k]) / px if px > 0 else 0)
            if sh <= 0:
                continue
            liq += sh * px * (1 - min(fill_cost(f, k, sh * px, kimp), 0.95))
            left -= sh
        if left > 0 and k1 + LIQ_DAYS >= len(cal) - 1:      # data ends before 60 sessions: value the rest at the last close
            liq += left * float(f["c"][last_k]) * (1 - min(fill_cost(f, last_k, left * float(f["c"][last_k]), kimp), 0.95))
        trades.append({"code": c, "in": cal[p["k_in"]], "out": "open", "pnl": p["sh"] * float(f["c"][k1]) + p["proceeds"] - p["spent"],
                       "spent": p["spent"], "peak": p["peak"] / p["px0"], "tag": p["tag"]})
    eq = np.array(eq_curve)
    dd = float(np.min(eq / np.maximum.accumulate(eq) - 1)) if len(eq) else 0.0
    return {"marks": [m / money for m in marks], "mark": mark_end / money, "liq": liq / money, "dd": dd, "trades": trades,
            "cut": k1 + LIQ_DAYS >= len(cal)}


# ------------------------------------------------------------------------------------------------ runs
def path_starts(a, b):
    cal = G["cal"]
    out = []
    for k in range(1, len(cal)):
        m = cal[k][:7]
        if cal[k - 1][:7] != m and a <= m <= b:
            out.append(k)
    return out


def run_cfg(args):
    cfg, money, kimp, starts = args
    cal = G["cal"]
    res = []
    for j, k0 in enumerate(starts):
        k1 = min(k0 + YEAR - 1, len(cal) - 1)
        if cfg["sig"] == "X200":
            a = G["axa"]
            seg = a[k0:k1 + 1]
            marks = [float(a[k] / a[k0]) for k in range(k0, k1 + 1) if k == k1 or cal[k][:7] != cal[min(k + 1, len(cal) - 1)][:7]]
            dd = float(np.min(seg / np.maximum.accumulate(seg) - 1))
            res.append({"marks": marks, "mark": float(seg[-1] / seg[0]), "liq": float(seg[-1] / seg[0]), "dd": dd, "trades": [],
                        "start": cal[k0], "cut": False, "full": k1 == k0 + YEAR - 1})
            continue
        r = simulate(k0, k1, cfg, money, kimp, seed=1000 * j + 7)
        r["start"] = cal[k0]
        r["full"] = k1 == k0 + YEAR - 1
        res.append(r)
    return cfg, money, kimp, res


def streak(trades):
    best = cur = 0
    for t in sorted(trades, key=lambda t: t["out"]):
        if t["pnl"] < 0:
            cur += 1; best = max(best, cur)
        else:
            cur = 0
    return best


def bootstrap(paths, rng):
    """Year = 4 blocks of 3 months; block j from months 3j..3j+2 of a random real path."""
    usable = [p for p in paths if len(p["marks"]) >= 12]
    if len(usable) < 10:
        return np.array([])
    seq = []
    for p in usable:
        m = [1.0] + p["marks"][:12]
        seq.append([m[3 * (j + 1)] / m[3 * j] if m[3 * j] > 0 else 0.0 for j in range(4)])
    seq = np.array(seq)
    hair = np.array([p["liq"] / p["mark"] if p["mark"] > 0 else 0.0 for p in usable])
    idx = rng.integers(0, len(usable), size=(N_BOOT, 4))
    x = np.prod(seq[idx, np.arange(4)], axis=1) * hair[idx[:, 3]]
    return x


def kelly(x):
    if len(x) == 0:
        return None
    best_f, best_g = 0.0, 0.0
    for f in np.linspace(0, 1, 101)[1:]:
        g = float(np.mean(np.log(np.maximum(1 + f * (x - 1), 1e-12))))
        if g > best_g:
            best_f, best_g = float(f), g
    return best_f


def summarise(paths, rng, base10=None):
    partial = sum(1 for p in paths if not p.get("full", True))
    paths = [p for p in paths if p.get("full", True)]
    liq = np.array([p["liq"] for p in paths]); mk = np.array([p["mark"] for p in paths])
    bs = bootstrap(paths, rng)
    trades = [t for p in paths for t in p["trades"]]
    gains = [max(t["pnl"], 0) for t in trades]
    top_share = []
    for p in paths:
        g = [max(t["pnl"], 0) for t in p["trades"]]
        if sum(g) > 0:
            top_share.append(max(g) / sum(g))
    hit10 = float(np.mean([t["peak"] >= 10 for t in trades])) if trades else None
    f = kelly(bs) if len(bs) else None
    q = f / 4 if f is not None else None

    def P(a, cond):
        return round(100 * float(np.mean(cond(a))), 2) if len(a) else None
    out = {"paths": len(paths), "partial_years_left_out": partial,
           "real": {"median": round(float(np.median(liq)), 3) if len(liq) else None, "p10x": P(liq, lambda a: a >= 10),
                    "p100x": P(liq, lambda a: a >= 100), "p_half": P(liq, lambda a: a <= 0.5), "p_ruin": P(liq, lambda a: a <= 0.1),
                    "mark_median": round(float(np.median(mk)), 3) if len(mk) else None, "mark_p10x": P(mk, lambda a: a >= 10),
                    "mark_p100x": P(mk, lambda a: a >= 100), "best": round(float(liq.max()), 2) if len(liq) else None},
           "boot": {"median": round(float(np.median(bs)), 3) if len(bs) else None, "p10x": P(bs, lambda a: a >= 10),
                    "p100x": P(bs, lambda a: a >= 100), "p_half": P(bs, lambda a: a <= 0.5), "p_ruin": P(bs, lambda a: a <= 0.1),
                    "q01": round(float(np.quantile(bs, 0.01)), 3) if len(bs) else None,
                    "q99": round(float(np.quantile(bs, 0.99)), 2) if len(bs) else None},
           "longest_losing_streak": {"median": int(np.median([streak(p["trades"]) for p in paths])) if paths else None,
                                     "max": max([streak(p["trades"]) for p in paths], default=None)},
           "max_dd": {"median_pct": round(100 * float(np.median([p["dd"] for p in paths])), 1) if paths else None,
                      "worst_pct": round(100 * float(min(p["dd"] for p in paths)), 1) if paths else None},
           "top_trade_share_median": round(float(np.median(top_share)), 3) if top_share else None,
           "trades": len(trades), "trades_per_year": round(len(trades) / max(len(paths), 1), 1),
           "hit_rate_pct": round(100 * float(np.mean([t["pnl"] > 0 for t in trades])), 1) if trades else None,
           "ten_bagger_entries_pct": round(100 * hit10, 2) if hit10 is not None else None,
           "kelly": f, "suggest_share_pct": round(100 * q, 1) if q is not None else None,
           "worst_case_loss_of_total_pct": round(100 * q * (1 - float(np.quantile(bs, 0.01))), 1) if q is not None and len(bs) else None}
    return out


def configs():
    out = []
    for s in SIGNALS:
        for pl in PLAYS:
            for n in NS:
                for st in STOPS:
                    out.append({"sig": s, "base": s, "play": pl, "n": n, "stop": st})
    for n in NS:
        out.append({"sig": "RAND", "play": "C", "n": n, "stop": 1.0})
        for s in SIGNALS:
            out.append({"sig": "SIGHOLD", "base": s, "play": "C", "n": n, "stop": 1.0})
    out.append({"sig": "X200", "play": "C", "n": 1, "stop": 1.0})
    return out


def name(cfg):
    if cfg["sig"] in ("RAND", "X200"):
        return f"{cfg['sig']}|N{cfg['n']}"
    if cfg["sig"] == "SIGHOLD":
        return f"SIG-{cfg['base']}|N{cfg['n']}"
    return f"{cfg['sig']}|{cfg['play']}|N{cfg['n']}|S{int(cfg['stop'] * 100)}"


def main():
    t0 = time.time()
    build()
    main_starts = path_starts(START_MAIN, END_MAIN)
    ub_starts = path_starts(START_UB, END_UB)
    jobs = []
    for cfg in configs():
        for money in MONEY:
            for kimp in KS:
                jobs.append((cfg, money, kimp, main_starts))
            jobs.append((cfg, money, 1.0, ub_starts) + ())
    # zero-cost, unlimited-capacity check for the reason test (k = 0 on A$100k: capacity still binds a little)
    for cfg in configs():
        if cfg["sig"] in SIGNALS:
            jobs.append((cfg, 100_000.0, 0.0, main_starts))
    print(f"[82] {len(jobs)} runs x ~{len(main_starts)} paths", flush=True)
    results = {}
    workers = max(1, (os.cpu_count() or 2))
    import multiprocessing
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as ex:
        for n, (cfg, money, kimp, res) in enumerate(ex.map(run_cfg, jobs, chunksize=4)):
            tag = "ub" if res and res[0]["start"] < START_MAIN else "main"
            results[(name(cfg), money, kimp, tag)] = res
            if n % 100 == 0:
                print(f"[82] {n}/{len(jobs)} runs, {time.time() - t0:.0f}s", flush=True)
    rng = np.random.default_rng(82)
    table = {}
    for (nm, money, kimp, tag), res in results.items():
        table[f"{nm}|{int(money)}|k{kimp}|{tag}"] = summarise(res, rng)
    # verdicts
    base = {n: table.get(f"RAND|N{n}|100000|k1.0|main") for n in NS}
    verdict = {}
    for s in SIGNALS:
        for pl in PLAYS:
            wins = 0
            for n in NS:
                for st in STOPS:
                    r = table.get(f"{s}|{pl}|N{n}|S{int(st * 100)}|100000|k1.0|main")
                    b = base[n]
                    if r and b and r["real"]["median"] is not None and r["real"]["median"] > b["real"]["median"] and \
                            (r["boot"]["p10x"] or 0) > (b["boot"]["p10x"] or 0):
                        wins += 1
            verdict[f"{s}|{pl}"] = {"versions_beating_rand": wins}
    # PBO over the 100 configs (A$100k, k = 1, 2017+), each path's log multiple as one period
    try:
        import validate
        cols, names = [], []
        for cfg in configs():
            if cfg["sig"] in SIGNALS:
                res = results[(name(cfg), 100_000.0, 1.0, "main")]
                cols.append([math.log(max(p["liq"], 1e-6)) for p in res if p.get("full", True)]); names.append(name(cfg))
        M = np.array(cols).T
        pbo = validate.pbo_cscv(M, S=16, metric="mean")
    except Exception as e:
        pbo = {"pbo": None, "error": type(e).__name__}
    for key, v in verdict.items():
        w = v["versions_beating_rand"]
        v["verdict"] = "有效" if w >= 3 and pbo.get("pbo") is not None and pbo["pbo"] < 0.5 else ("巧合" if w in (1, 2) else "无效")
    best100 = max((v["boot"]["p100x"] or 0, k) for k, v in table.items() if k.endswith("|main") and "|k1.0|" in k and not k.startswith(("RAND", "X200", "SIG")))
    out = {"round": 82, "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Rules fixed")[1][:6000],
           "paths_main": len(main_starts), "paths_upper_bound": len(ub_starts), "stocks": len(G["F"]),
           "signal_events": {t: sum(1 for v in G["events"].values() for tg, _ in v if tg == t) for t in "ABCD"},
           "no_touch_scored_announcements": G["n_scored"], "no_touch_codes": len(G["ntk"]),
           "index_deletion_days": len(G["dele"]), "table": table, "verdict": verdict, "pbo": pbo,
           "best_p100x_main_k1": {"pct": best100[0], "config": best100[1]}, "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[82] done", out["seconds"], "s", flush=True)   # numbers stay in the encrypted file


if __name__ == "__main__":
    main()
