"""Round 103: a holding version of round 98's R4 (bottom + volume breakout to a 1-year high) for an A$10k satellite account.

Frank 2026-11-26: can R4 be held as a A$10k satellite position, how should it be sold, and how many names to hold?
Rules fixed 2026-11-26 BEFORE any result was seen (committed to git before the first run; no re-tuning afterwards):

  1. Signal: round 98's R4, same functions (study98.sma / roll_max / roll_min) and the same episode logic:
       setup = close at the lowest close of the past 500 sessions while <= 50% of the 3-year high; episode = 250 sessions;
       R4 = close above the highest high of the previous 250 sessions with 20-day traded value >= 2x the 250-day average,
       counted once per episode, only on days the stock passes the liquidity tier.
     Signals from 2017-01-01 on. Two periods: P1 2017-01-01..2020-12-31, P2 2021-01-01..last day (P2 = out of sample).
  2. Liquidity: 20-session median traded value >= A$20k (main). Also A$10k and A$50k tiers. The tier is part of the
     signal (an R4 on a day below the tier does not count and does not use up the episode), so each tier is its own run.
  3. Exits (decided on the close, executed at the stock's next open):
       X1  hold 252 sessions;
       X2  (main) sell when the close is 40% below the highest close since entry;
       X3  sell after 5 consecutive closes below the 200-day simple average;
       X4  sell half at 2x the entry price (limit: filled at 2x, or at the open if it gaps above 2x), the rest by X2.
     A stock that stops trading is sold at its last close (delisted stocks are in; a real zero would be worse).
  4. Account: A$10,000, at most 5 positions, each bought with min(cash, equity / 5). A new signal is bought only when a
     slot is free; same-day signals are taken by 20-day median traded value, largest first. An order is capped at 5% of
     the stock's 20-day median traded value (CLAUDE.md); orders under A$200 are skipped.
  5. Costs per side: IBKR ASX commission max(A$6, 0.08%) + half the spread + impact.
       Spread model = 2 ticks (round 71: one tick per side), so half spread = 1 tick / price.
       Impact = 0.5 x 20-day volatility of daily returns x sqrt(order / 20-day average traded value).
     Calibration with IBKR depth snapshots (out/depth): checked 2026-11-26 -- no snapshot exists yet (every slot since
     2026-11-24 is logged "盈透断线 / Gateway 没登录"), so the spread is reported as 价差未校准.
  6. Controls:
       C1 底部观察: for every signal the strategy actually takes, the same account instead buys a random stock that is on
          the same day's 底部观察 list (inside a setup episode, passes the tier, no R1 and no R4 yet), same exits, same
          ordering; 20 random draws, averaged.
       C2 buy-and-hold AXSO (Small Ordinaries price index; no dividends -- flatters the strategy slightly).
     Report before tax and after 32.5% tax (realised gains taxed each financial year to 30 June, losses carried forward,
     50% CGT discount for lots held > 365 days; AXSO taxed once at the end on the same basis).
  7. Trials: 4 exits x 3 tiers = 12, registered in research/trials.json before running. validate.py's three gates.
     Verdict 有效 only if the main version (X2, A$20k): t >= 3 in both periods (monthly account return minus the C1
     average, t over months = clustered by month); P2 annual return above AXSO's; P2 max drawdown no more than 10 points
     deeper than AXSO's; and the gates pass. Anything less = 持有版不成立 (with the reason).
  Also reported per combo: annual return, max drawdown, win rate, average holding days, max names held at once,
  expected value per A$1 risked, average win / loss, biggest winner's share of profit, longest losing streak, worst
  trade, worst month, longest time to recover.
"""
import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

import study
import study98 as S
import validate as V

OUT = Path(os.environ.get("STUDY103_OUT", "out/study103_results.json"))
START, SPLIT = "2017-01-01", "2021-01-01"
TIERS = (10_000, 20_000, 50_000)
EXITS = ("X1", "X2", "X3", "X4")
MAIN = ("X2", 20_000)
CASH0, SLOTS, TAX = 10_000.0, 5, 0.325
N_CTRL = 20


def tick(p):
    return 0.001 if p < 0.10 else 0.005 if p < 2 else 0.01


def side_cost(value, raw, sigma, avgval):
    comm = max(6.0, 0.0008 * value)
    half = tick(raw) / raw if raw > 0 else 1.0
    imp = 0.5 * (sigma if sigma == sigma else 0.05) * math.sqrt(value / avgval) if avgval > 0 else 0.05
    return comm + value * (half + min(imp, 0.2))


# ------------------------------------------------------------------ data
class Stock:
    __slots__ = ("code", "d", "idx", "o", "h", "c", "raw", "s200", "med20", "v20", "sig", "last")

    def __init__(self, code, rows):
        self.code = code
        self.d = [r[0] for r in rows]
        self.idx = {x: i for i, x in enumerate(self.d)}
        self.o = np.array([r[1] for r in rows]); self.h = np.array([r[2] for r in rows])
        self.c = np.array([r[4] for r in rows]); self.raw = np.array([r[6] for r in rows])
        self.last = len(rows) - 1


def scan(rows, tier):
    """Round 98 episode loop for one tier: R4 days, and 底部观察 days (in episode, tradable, no R1/R4 yet)."""
    d = [r[0] for r in rows]
    lo = np.array([r[3] for r in rows]); h = np.array([r[2] for r in rows]); c = np.array([r[4] for r in rows])
    v = np.array([r[5] for r in rows]); raw = np.array([r[6] for r in rows])
    val = raw * v
    minc500, maxh750 = S.roll_min(c, 500), S.roll_max(h, 750)
    maxh250_prev = np.concatenate([[np.nan], S.roll_max(h, 250)[:-1]])
    v20, v250 = S.sma(val, 20), S.sma(val, 250)
    from numpy.lib.stride_tricks import sliding_window_view
    med20 = np.full(len(c), np.nan)
    if len(c) >= 20:
        med20[19:] = np.median(sliding_window_view(val, 20), axis=1)
    tradable = (raw >= 0.01) & (med20 >= tier)
    sig, watch = [], []
    ep_start, ep_low, ep_pre, seen = None, None, None, set()
    for i in range(750, len(c) - 1):
        if c[i] <= minc500[i] and c[i] <= 0.5 * maxh750[i]:
            lowday = i - int(np.argmin(lo[max(0, i - 59):i + 1][::-1]))
            ep_start, ep_low, seen = i, float(np.min(lo[max(0, i - 59):i + 1])), set()
            ep_pre = c[max(0, lowday - 20)]
        if ep_start is not None and i - ep_start > 250:
            ep_start = None
        if ep_start is None or not tradable[i]:
            continue
        r4 = "R4" not in seen and maxh250_prev[i] == maxh250_prev[i] and c[i] > maxh250_prev[i] and v250[i] > 0 \
            and v20[i] >= 2 * v250[i]
        r1 = "R1" not in seen and c[i] >= 1.5 * ep_low and c[i] > ep_pre
        if d[i] >= START and not seen and not r4 and not r1:
            watch.append((d[i], i, float(med20[i])))
        if r1:
            seen.add("R1")
        if r4:
            seen.add("R4")
            if d[i] >= START:
                sig.append((d[i], i, float(med20[i])))
    return sig, watch, med20, v20


def load_all():
    stocks, sigs, watch = {}, {t: defaultdict(list) for t in TIERS}, {t: defaultdict(list) for t in TIERS}
    for k, fp in enumerate(sorted(study.CACHE.glob("*.AU.json"))):
        rows = study.load(fp.name[:-5])
        if len(rows) < 800 or rows[-1][0] < START:
            continue
        code = fp.name[:-8]
        st = None
        for t in TIERS:
            sg, wt, med20, v20 = scan(rows, t)
            if not sg and not wt:
                continue
            if st is None:
                st = Stock(code, rows)
                from numpy.lib.stride_tricks import sliding_window_view
                r = np.diff(np.log(np.maximum(st.c, 1e-9)))
                st.sig = np.full(len(st.c), np.nan)
                if len(r) >= 20:
                    st.sig[20:] = sliding_window_view(r, 20).std(axis=1)
                st.s200 = S.sma(st.c, 200)
                st.med20, st.v20 = med20, v20
            for d, i, m in sg:
                sigs[t][d].append((m, code, i))
            for d, i, m in wt:
                watch[t][d].append((m, code, i))
        if st is not None:
            stocks[code] = st
        if k % 500 == 0:
            print(f"[103] {k} files, {len(stocks)} kept", flush=True)
    return stocks, sigs, watch


# ------------------------------------------------------------------ account
def simulate(cal, stocks, picks, exit_rule, taxed, end_day):
    """picks: date -> list of (med20, code, row) in the order to take them. Returns the account record."""
    cash, hold, pend_sell, pend_buy = CASH0, [], [], []
    eq, trades, fy_gain, carry, max_names = [], [], defaultdict(float), 0.0, 0
    lots_tax = []          # (day, gain, held_days) realised

    def fy(day):
        return int(day[:4]) + (1 if day[5:7] >= "07" else 0)

    def realise(p, frac, px_net_value, day):
        nonlocal cash
        basis = p["basis"] * frac
        cash += px_net_value
        p["basis"] -= basis
        gain = px_net_value - basis
        held = (np.datetime64(day) - np.datetime64(p["day"])).astype(int)
        fy_gain[fy(day)] += gain * (0.5 if gain > 0 and held > 365 else 1.0)
        p["realised"] += gain
        p["proceeds"] += px_net_value

    def sell(p, frac, px, row, day):
        st = stocks[p["code"]]
        value = p["units"] * frac * px
        cst = side_cost(value, st.raw[row], st.sig[row], st.v20[row] if st.v20[row] == st.v20[row] else 1.0)
        realise(p, frac, max(0.0, value - cst), day)
        p["units"] *= (1 - frac)

    last_fy = fy(cal[0])
    for D in cal:
        # tax at the start of a new financial year
        if taxed and fy(D) != last_fy:
            g = fy_gain.pop(last_fy, 0.0) + carry
            if g > 0:
                cash -= TAX * g
                carry = 0.0
            else:
                carry = g
            last_fy = fy(D)
        # 1. orders at today's open
        for p in list(pend_sell):
            st = stocks[p["code"]]
            r = st.idx.get(D)
            if r is None:
                continue
            sell(p, 1.0, st.o[r], r, D)
            p["exit_day"], p["hold_days"] = D, r - p["row"]
            trades.append(p); hold.remove(p); pend_sell.remove(p)
        for code, row in pend_buy:
            st = stocks[code]
            r = row + 1
            if r > st.last or st.d[r] != D:      # not trading on the next session: the signal is dropped
                continue
            mark = cash + sum(q["units"] * stocks[q["code"]].c[q["mi"]] for q in hold)
            amt = min(cash, mark / SLOTS, 0.05 * st.med20[row])
            if amt < 200 or len(hold) >= SLOTS:
                continue
            cst = side_cost(amt, st.raw[r], st.sig[row], st.v20[row] if st.v20[row] == st.v20[row] else 1.0)
            units = (amt - cst) / st.o[r]
            if units <= 0:
                continue
            cash -= amt
            hold.append({"code": code, "row": r, "day": D, "px": st.o[r], "units": units, "basis": amt, "peak": st.c[r],
                         "below": 0, "half": False, "realised": 0.0, "proceeds": 0.0, "cost": amt, "mi": r})
        pend_buy = []
        # 2. on today's close: update, decide exits
        for p in hold:
            st = stocks[p["code"]]
            r = st.idx.get(D)
            if r is None:
                continue
            p["mi"] = r
            if p in pend_sell:
                continue
            if exit_rule == "X4" and not p["half"] and st.h[r] >= 2 * p["px"]:
                fill = st.o[r] if st.o[r] >= 2 * p["px"] and r != p["row"] else 2 * p["px"]
                sell(p, 0.5, fill, r, D)
                p["half"] = True
            p["peak"] = max(p["peak"], st.c[r])
            p["below"] = p["below"] + 1 if st.s200[r] == st.s200[r] and st.c[r] < st.s200[r] else 0
            out = (exit_rule == "X1" and r - p["row"] >= 252) or \
                  (exit_rule in ("X2", "X4") and st.c[r] <= 0.6 * p["peak"]) or \
                  (exit_rule == "X3" and p["below"] >= 5)
            if r == st.last and st.d[r] < end_day:        # stopped trading: sell at the last close
                sell(p, 1.0, st.c[r], r, D)
                p["exit_day"], p["hold_days"], p["delisted"] = D, r - p["row"], True
                trades.append(p)
                p["gone"] = True
            elif out and r < st.last:
                pend_sell.append(p)
        hold = [p for p in hold if not p.get("gone")]
        # 3. today's signals -> tomorrow's open
        free = SLOTS - len(hold) + len(pend_sell)
        held = {p["code"] for p in hold}
        for m, code, row in picks.get(D, []):
            if free <= 0:
                break
            if code in held:
                continue
            pend_buy.append((code, row)); held.add(code); free -= 1
        val = cash + sum(p["units"] * stocks[p["code"]].c[p["mi"]] for p in hold)
        max_names = max(max_names, len(hold))
        eq.append(val)
    # close what is still open at the last mark (for trade statistics only; equity already marked)
    open_pos = []
    for p in hold:
        st = stocks[p["code"]]
        p2 = dict(p)
        p2["proceeds"] = p["proceeds"] + p["units"] * st.c[p["mi"]]
        p2["realised"] = p2["proceeds"] - p["cost"]
        p2["hold_days"], p2["open"] = p["mi"] - p["row"], True
        open_pos.append(p2)
    if taxed:   # liquidation tax at the end on what was never realised (same basis), so after-tax is comparable
        g = sum(fy_gain.values()) + carry + sum(p["realised"] * 0.5 if p["realised"] > 0 else p["realised"] for p in open_pos)
        if g > 0:
            eq[-1] -= TAX * g
    return {"eq": np.array(eq), "trades": trades + open_pos, "max_names": max_names}


# ------------------------------------------------------------------ statistics
def seg(cal, eq, lo, hi):
    ix = [i for i, d in enumerate(cal) if lo <= d < hi]
    return ix[0], ix[-1]


def cagr(cal, eq, a, b):
    yrs = (np.datetime64(cal[b]) - np.datetime64(cal[a])).astype(int) / 365.25
    base = eq[a - 1] if a > 0 else CASH0
    return (eq[b] / base) ** (1 / yrs) - 1 if yrs > 0 and base > 0 and eq[b] > 0 else -1.0


def mdd(eq, a, b):
    x = eq[a:b + 1]
    pk = np.maximum.accumulate(x)
    return float(np.min(x / pk - 1))


def recover_days(cal, eq):
    pk, pk_i, best = eq[0], 0, 0
    for i, v in enumerate(eq):
        if v >= pk:
            best = max(best, i - pk_i)
            pk, pk_i = v, i
    return max(best, len(eq) - 1 - pk_i)


def monthly(cal, eq):
    m = {}
    for d, v in zip(cal, eq):
        m[d[:7]] = v
    ks = sorted(m)
    prev, out = CASH0, {}
    for k in ks:
        out[k] = m[k] / prev - 1 if prev > 0 else 0.0
        prev = m[k]
    return out


def trade_stats(trades):
    rets = [t["realised"] / t["cost"] for t in trades if t["cost"] > 0]
    if not rets:
        return {"n": 0}
    w = [r for r in rets if r > 0]; l = [r for r in rets if r <= 0]
    prof = [t["realised"] for t in trades if t["realised"] > 0]
    streak = cur = 0
    for t in sorted(trades, key=lambda x: x["day"]):
        cur = cur + 1 if t["realised"] <= 0 else 0
        streak = max(streak, cur)
    hit = len(w) / len(rets)
    aw, al = (float(np.mean(w)) if w else 0.0), (float(np.mean(l)) if l else 0.0)
    return {"n": len(rets), "win_rate": round(hit, 3), "avg_win": round(aw, 3), "avg_loss": round(al, 3),
            "ev_per_1": round(hit * aw + (1 - hit) * al, 3),
            "best_share": round(max(prof) / sum(prof), 3) if prof else None,
            "losing_streak": streak, "worst_trade": round(min(rets), 3), "best_trade": round(max(rets), 3),
            "avg_hold_days": round(float(np.mean([t["hold_days"] for t in trades])), 1),
            "delisted": sum(1 for t in trades if t.get("delisted")), "x2_trades": sum(1 for r in rets if r >= 1.0)}


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else None


def summarise(cal, res, p1, p2):
    eq = res["eq"]
    mo = monthly(cal, eq)
    out = {"final": round(float(eq[-1])), "max_names": res["max_names"], "recover_days": recover_days(cal, eq),
           "worst_month": round(min(mo.values()), 3) if mo else None}
    for name, (a, b) in (("all", (0, len(eq) - 1)), ("P1", p1), ("P2", p2)):
        out[name] = {"cagr": round(cagr(cal, eq, a, b), 4), "mdd": round(mdd(eq, a, b), 4)}
    out.update(trade_stats(res["trades"]))
    return out, mo


def main():
    t0 = time.time()
    stocks, sigs, watch = load_all()
    ax = study.load("AXSO.INDX")
    axd = {r[0]: r[4] for r in ax}
    end_day = max(max(s.d[-1] for s in stocks.values()), ax[-1][0] if ax else "0")
    cal = [d for d in sorted(axd) if d >= START]
    p1, p2 = seg(cal, None, START, SPLIT), seg(cal, None, SPLIT, "9999")
    axe = np.array([axd[d] / axd[cal[0]] * CASH0 for d in cal])
    ax_mo = monthly(cal, axe)
    g = (axe[-1] - CASH0)
    ax_after = axe.copy(); ax_after[-1] -= TAX * 0.5 * g if g > 0 else 0
    res = {"round": 103, "rules": "see study103.py header (fixed 2026-11-26 before results)",
           "spread": "价差未校准：out/depth 还没有任何盘口快照（11/24 起每次都是 盈透断线 / Gateway 没登录）",
           "axso": {"P1": {"cagr": round(cagr(cal, axe, *p1), 4), "mdd": round(mdd(axe, *p1), 4)},
                    "P2": {"cagr": round(cagr(cal, axe, *p2), 4), "mdd": round(mdd(axe, *p2), 4)},
                    "all": {"cagr": round(cagr(cal, axe, 0, len(cal) - 1), 4), "mdd": round(mdd(axe, 0, len(cal) - 1), 4)},
                    "final_after_tax": round(float(ax_after[-1]))},
           "signals": {str(t): sum(len(v) for v in sigs[t].values()) for t in TIERS}, "combos": {}}
    grid = {}
    rng = np.random.default_rng(103)
    for t in TIERS:
        picks = {d: sorted(v, reverse=True) for d, v in sigs[t].items()}
        for x in EXITS:
            pre = simulate(cal, stocks, picks, x, False, end_day)
            post = simulate(cal, stocks, picks, x, True, end_day)
            s_pre, mo = summarise(cal, pre, p1, p2)
            s_post, _ = summarise(cal, post, p1, p2)
            # control C1: same days, same number of names, random 底部观察 stocks
            taken = defaultdict(int)
            for p in pre["trades"]:
                st = stocks[p["code"]]
                taken[st.d[p["row"] - 1]] += 1
            cmo = []
            for k in range(N_CTRL):
                cp = {}
                for d, n in taken.items():
                    pool = watch[t].get(d, [])
                    if pool:
                        sel = rng.choice(len(pool), size=min(len(pool), n + 3), replace=False)
                        cp[d] = sorted((pool[j] for j in sel), reverse=True)
                cr = simulate(cal, stocks, cp, x, False, end_day)
                cmo.append(monthly(cal, cr["eq"]))
                if k == 0:
                    c_sum, _ = summarise(cal, cr, p1, p2)
            months = sorted(mo)
            cmean = {m: float(np.mean([c.get(m, 0.0) for c in cmo])) for m in months}
            diff = {m: mo[m] - cmean[m] for m in months}
            tp = {"P1": tstat([diff[m] for m in months if m < SPLIT[:7]]),
                  "P2": tstat([diff[m] for m in months if m >= SPLIT[:7]])}
            ctrl_eq = np.cumprod([1 + cmean[m] for m in months]) * CASH0
            key = f"{x}_{t // 1000}k"
            grid[key] = [mo[m] for m in months]
            res["combos"][key] = {"pre_tax": s_pre, "after_tax": s_post, "t_vs_ctrl": {k: (round(v, 2) if v else None) for k, v in tp.items()},
                                  "ctrl_first_draw": c_sum, "ctrl_mean_final": round(float(ctrl_eq[-1])),
                                  "vs_axso_monthly_t": tstat([mo[m] - ax_mo.get(m, 0.0) for m in months])}
            print(f"[103] {key}: done ({time.time() - t0:.0f}s)", flush=True)
    M = np.array([grid[k] for k in grid]).T
    main_key = f"{MAIN[0]}_{MAIN[1] // 1000}k"
    mc = res["combos"][main_key]
    tmin = min((abs(v) if v is not None else 0) for v in mc["t_vs_ctrl"].values())
    same_sign = all(v is not None and v > 0 for v in mc["t_vs_ctrl"].values())
    dsr = V.deflated_sharpe(np.array(grid[main_key]), 12)
    pbo = V.pbo_cscv(M)
    lab, why = V.label(tmin if same_sign else 0.0, dsr.get("dsr"), pbo.get("pbo"))
    p2s, axp2 = mc["pre_tax"]["P2"], res["axso"]["P2"]
    checks = {"t>=3 both periods (positive)": bool(same_sign and tmin >= 3),
              "P2 annual > AXSO": bool(p2s["cagr"] > axp2["cagr"]),
              "P2 max drawdown within 10 pts of AXSO": bool(p2s["mdd"] >= axp2["mdd"] - 0.10),
              "gates": lab == "✅"}
    res["main"] = {"key": main_key, "checks": checks, "gates": {"label": lab, "why": why, "dsr": dsr, "pbo": pbo.get("pbo")},
                   "verdict": "有效" if all(checks.values()) else "持有版不成立"}
    res["seconds"] = round(time.time() - t0)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"[103] verdict {res['main']['verdict']} in {res['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
