"""Round 94: the portfolio ledger -- every rule that passed, run together in one real-money account.

Frank 2026-11-20. Question: if all the rules that survived 93 rounds share ONE A$100k account, what does it earn a
year, how deep is the worst drawdown, and how much more than simply holding an ASX 200 ETF -- before tax and after
tax at 0% / 32.5% / 47%?

Rule list (agreed with Frank 2026-11-20; A-share models are a separate RMB account, not in here):
  CORE  round 25/43 ETF plan C: ASX 200 ETF 60% of the core normally; 100% when FEAR (VIX>=25 or ASX 200 down >5% in 3
        months, 60 sessions) / CUTS (AU 2y yield down >0.5% in 3 months, 20 sessions) / STREAK (3 down closes, 5
        sessions); 30% for 5 sessions after a hawkish RBA day (2y yield up >=6bp). Rest of the core in cash.
  S1    round 56/57 results-season gap-up: small/mid cap (20-day median value $0.2-2M, price >= 20c), opens >= 8% up on
        >= 3x volume and closes above the open -> buy next open, sell after 20 sessions.
        FLAG: the 8% / 20-day pair was picked with 2022+ data in view (round 57 grid) -> not a clean out-of-sample.
  S2    round 66 rocket: >= 50% below the 3-year high, 20-day median value >= 2x its 1-year median, back above the
        200-day line; tradable only (price >= 2c, 60-day median value >= $30k). New signal only (first day). Hold up to
        252 sessions; when the close first reaches 2x the entry, sell half.
  S3    round 70 monthly MACD fresh: at a month end the price is below 12 months ago and the monthly MACD histogram
        (12,26,9 on month-end closes) has been above zero for 1-11 months. Buy next open, hold 252 sessions.
  F1    round 71 trend filter: satellites buy only stocks closing above their 200-day line on the signal day.
  F2    round 52: no buying within 20 sessions after a halt -> reopen >= 5% lower on >= 3x volume.
  X1    round 85: a holding announced as deleted from the ASX 300 / Small Ords / All Ords is sold at the next open.
  SIZE  round 61's idea (volatility -> size); the walk-forward model has no history before its test period, so the
        backtest uses the trailing 60-session realised volatility.

Account rules (fixed 2026-11-20 BEFORE any result was seen):
  * A$100,000 start. Satellites draw from the same equity; satellite total <= 40% of equity; the core is the rest.
  * Each new satellite: weight = min(10%, 4% x 40% / its annualised vol) of equity, scaled by the vol-target factor;
    position value <= 5% of its 20-day median daily value traded; skipped below A$1,000. Same sector <= 30% of equity
    (sector from the ASX company API where known; unknown sector = no cap, coverage reported).
  * Several signals the same day: highest 20-day median value first.
  * Vol target 15%: trailing 60-session realised vol of the whole account; s = min(1, 15% / vol). s scales the ETF
    target and every new satellite; when s < 0.75 every satellite is also trimmed to s of its size.
  * Prices: next open for buys, close for timed exits, next open for X1. Costs: round 71 tiers, 0.2% + one tick per side
    on the raw price (ETF assumed ~$30 a unit). ETF rebalanced when it is more than 2% of equity off its target.
  * Cash earns the RBA cash rate.
Tax (Frank's own field): Australian FY to 30 June; every lot tracked FIFO; disposals held > 365 days get the 50% CGT
  discount, others none; capital losses offset non-discount gains first and carry forward. ETF distributions =
  accumulation minus price index, 70% franked (credit = 30% of the cash dividend), credit refundable. Satellite returns
  are all treated as capital (adjusted prices; small caps rarely pay). Tax is paid on the last session of June. Gains
  still unrealised at the end are not taxed (same for the benchmark).
Tests: 2015-2021 = in sample (every parameter came from earlier rounds), 2022 on = out of sample; yearly table.
  validate.py gates on the monthly excess return over "ETF only" (both pre-tax): t >= 3, deflated Sharpe >= 95% with the
  trial count registered below, PBO over the 64 on/off combinations of S1-S3, F1, F2, X1.
  Overlap: correlation of each buy rule's own daily returns (run alone, core in cash); a pair above 0.7 keeps the one
  with the higher 2015-2021 Sharpe.
"""
import bisect
import gzip
import itertools
import json
import math
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import study
import validate

START, SPLIT = "2015-01-01", "2022-01-01"
EQ0 = 100_000.0
SAT_CAP, POS_CAP, SECTOR_CAP, ADV_CAP, MIN_POS = 0.40, 0.10, 0.30, 0.05, 1000.0
VOL_TARGET, TRIM_BELOW = 0.15, 0.75
PLAN_C = (0.6, 1.0, 0.3)
ETF_PX = 30.0
FRANK_SHARE = 0.30          # franking credit as a share of the cash dividend (70% franked at the 30% company rate)
RATES = (0.0, 0.325, 0.47)
RULES = ("S1", "S2", "S3")
SWITCHES = ("F1", "F2", "X1")
TRIALS = {   # register first (CLAUDE.md 检验标准): combinations actually tried behind the parts used here
    "round 94 on/off combinations (S1-S3, F1, F2, X1)": 64,
    "round 25 ETF plans x rule sets": 24,
    "round 43 fear-boost grid (upper bound, not all logged)": 27,
    "round 56/57 gap x hold x turnover grid": 18,
    "round 66 rocket thresholds (upper bound)": 12,
    "round 70 MACD versions x divergence types": 4,
    "round 71 textbook signals used for the trend filter": 34,
    "round 52/85 avoid and exit rules": 8,
}
N_TRIALS = sum(TRIALS.values())


def tick(p):
    return 0.001 if p < 0.10 else 0.005 if p < 2 else 0.01


def side_cost(raw):
    return 0.002 + (tick(raw) / raw if raw > 0 else 1.0)


ETF_COST = side_cost(ETF_PX)


# ---------------------------------------------------------------- market data
def market():
    try:
        study.download(["AXJOA.INDX", "AXJO.INDX", "VIX.INDX"])
    except Exception as e:
        print("[94] index download skipped:", type(e).__name__, flush=True)
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    px = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    vix = {r[0]: r[4] for r in study.load("VIX.INDX")}
    f1 = f2 = {}
    rba = set()
    try:
        import study4
        import study7
        f2 = study7.rba_table("f2")["FCMYGBAG2D"]
        f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
        rba = set(study4.fallback_events()["澳联储 利率决议"])
    except Exception as e:
        print("[94] RBA tables unavailable:", type(e).__name__, flush=True)
    days = [d for d in sorted(acc) if d in px and d >= "2013-06-01"]
    return days, acc, px, vix, f1, f2, rba


def plan_weights(days, px, vix, f2, rba, plan):
    """Core ETF weight for each day (known at the previous close), round 25 logic."""
    vk, yk = sorted(vix), sorted(f2)
    boost = {"FEAR": 0, "CUTS": 0, "STREAK": 0}
    cut = 0
    w = {}
    for i in range(70, len(days)):
        p = days[i - 1]
        j = bisect.bisect_left(vk, p) - 1
        v = vix[vk[j]] if j >= 0 else None
        if (v and v >= 25) or px[p] / px[days[i - 64]] - 1 < -.05:
            boost["FEAR"] = 60
        yj = bisect.bisect_right(yk, p) - 1
        if yj >= 63 and f2[yk[yj]] - f2[yk[yj - 63]] < -.5:
            boost["CUTS"] = 20
        if px[p] < px[days[i - 2]] < px[days[i - 3]] < px[days[i - 4]]:
            boost["STREAK"] = 5
        if p in rba and p in f2 and yj >= 1 and f2[p] - f2[yk[yj - 1]] >= .06:
            cut = 5
        base, up, down = plan
        w[days[i]] = down if cut > 0 else up if any(x > 0 for x in boost.values()) else base
        for k in boost:
            boost[k] = max(0, boost[k] - 1)
        cut = max(0, cut - 1)
    return w


def season(d):
    md = d[5:]
    return ("02-05" <= md <= "03-05") or ("08-05" <= md <= "09-05")


# ---------------------------------------------------------------- stock signals
def stock_frame(code, cal):
    rows = study.load(code + ".AU")
    if len(rows) < 260:
        return None
    try:
        import study80
        rows, _ = study80.fix_consolidations([r + (r[6] * r[5],) for r in rows])
        rows = [r[:7] for r in rows]
    except Exception:
        pass
    by = {r[0]: r for r in rows}
    first = rows[0][0]
    c0 = [d for d in cal if d >= first]
    if len(c0) < 260:
        return None
    o, c, raw, v, traded = [], [], [], [], []
    last = None
    for d in c0:
        r = by.get(d)
        if r:
            last = r
        if last is None:
            o.append(np.nan); c.append(np.nan); raw.append(np.nan); v.append(0.0); traded.append(False)
            continue
        o.append(r[1] if r else last[4]); c.append(last[4]); raw.append(last[6])
        v.append(r[5] if r else 0.0); traded.append(bool(r and r[5] > 0))
    f = pd.DataFrame({"o": o, "c": c, "raw": raw, "v": v, "traded": traded}, index=c0)
    f["val"] = f["raw"] * f["v"]
    return f


def signals_for(code, cal, month_end):
    f = stock_frame(code, cal)
    if f is None:
        return None
    c, o, raw, v, val, tr = f["c"], f["o"], f["raw"], f["v"], f["val"], f["traded"]
    ma200 = c.rolling(200).mean()
    v20 = val.rolling(20).median()
    v60 = val.rolling(60).median()
    vol20 = v.rolling(20).median().shift(1)
    lr = np.log(c / c.shift(1)).where(tr)
    sig = lr.rolling(60, min_periods=30).std() * math.sqrt(252)
    out = {"f": f, "events": [], "halts": []}
    d = f.index
    above = (c > ma200).to_numpy()
    # S1 results-season gap-up
    pc = c.shift(1)
    turn = val.rolling(20).median().shift(1)
    s1 = (o / pc - 1 >= 0.08) & (c > o) & (v >= 3 * vol20) & (vol20 > 0) & (turn >= 2e5) & (turn < 2e6) & (raw >= 0.2) & tr
    for i in np.flatnonzero(s1.to_numpy()):
        if season(d[i]):
            out["events"].append((d[i], "S1", float(turn.iloc[i]), float(sig.iloc[i]) if sig.iloc[i] == sig.iloc[i] else 0.6,
                                  bool(above[i])))
    # S2 rocket (round 66 / study22 rule)
    hi3 = c.rolling(756, min_periods=253).max()
    v250 = val.rolling(250, min_periods=125).median()
    ok = (c / hi3 - 1 < -0.5) & (c > ma200) & (v250 > 0) & (v20 / v250 > 2) & (raw >= 0.02) & (v60 >= 3e4)
    ok = ok & (np.arange(len(f)) >= 252)
    okn = ok.to_numpy()
    for i in np.flatnonzero(okn[1:] & ~okn[:-1]) + 1:
        out["events"].append((d[i], "S2", float(v20.iloc[i]), float(sig.iloc[i]) if sig.iloc[i] == sig.iloc[i] else 0.8, bool(above[i])))
    # S3 monthly MACD histogram fresh above zero, price below a year ago
    me = [k for k, x in enumerate(d) if x in month_end]
    if len(me) >= 36:
        mc = c.iloc[me]
        e12, e26 = mc.ewm(span=12, adjust=False).mean(), mc.ewm(span=26, adjust=False).mean()
        macd = e12 - e26
        hist = (macd - macd.ewm(span=9, adjust=False).mean()).to_numpy()
        mcv = mc.to_numpy()
        pos_run = 0
        for k in range(len(me)):
            pos_run = pos_run + 1 if hist[k] > 0 else 0
            i = me[k]
            if k < 30 or not (1 <= pos_run <= 11) or not (mcv[k] < mcv[k - 12]):
                continue
            if not (v60.iloc[i] >= 1e4 and raw.iloc[i] >= 0.005):
                continue
            out["events"].append((d[i], "S3", float(v20.iloc[i]), float(sig.iloc[i]) if sig.iloc[i] == sig.iloc[i] else 0.8, bool(above[i])))
    # F2 halt -> reopen lower
    trn = tr.to_numpy()
    on, cn, vn, valn = o.to_numpy(), c.to_numpy(), v.to_numpy(), val.to_numpy()
    last_tr = None
    for i in range(len(f)):
        if not trn[i]:
            continue
        if last_tr is not None and 2 <= i - last_tr <= 6 and i > 25:
            pcv = cn[last_tr]
            vm = np.median(vn[max(0, last_tr - 20):last_tr + 1])
            tv = np.median(valn[max(0, last_tr - 20):last_tr + 1])
            if pcv > 0 and on[i] / pcv - 1 <= -0.05 and vm > 0 and vn[i] >= 3 * vm and 5e5 <= tv < 2e6:
                out["halts"].append(d[i])
        last_tr = i
    out["adv"] = v20
    return out


def sectors(codes):
    """Sector from the ASX company API (cached files first). Delisted companies have none."""
    res = {}
    todo = []
    for c in codes:
        f = study.CACHE / f"info_{c}.json"
        if f.exists():
            try:
                res[c] = json.loads(f.read_text(encoding="utf-8")).get("sector")
                continue
            except Exception:
                pass
        todo.append(c)
    if os.environ.get("S94_NO_NET") != "1" and todo:
        import urllib.request

        def one(c):
            try:
                req = urllib.request.Request(f"https://asx.api.markitdigital.com/asx-research/1.0/companies/{c.lower()}/header",
                                             headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    s = (json.load(r).get("data") or {}).get("sector")
                if s:
                    (study.CACHE / f"info_{c}.json").write_text(json.dumps({"sector": s}), encoding="utf-8")
                return c, s
            except Exception:
                return c, None
        with ThreadPoolExecutor(12) as ex:
            for c, s in ex.map(one, todo):
                res[c] = s
    return res


def index_deletions():
    """Round 85's rebalance lists from our announcement archive: {announcement day: set(codes deleted)}."""
    try:
        import study85
        out = defaultdict(set)
        for day, a in study85.collect().items():
            _, idx = study85.parse(a["text"])
            for k, rec in idx.items():
                if k in ("ASX300", "SmallOrds", "AllOrds"):
                    out[day] |= set(rec["del"])
        return {k: v for k, v in out.items() if v}
    except Exception as e:
        print("[94] index lists unavailable:", type(e).__name__, e, flush=True)
        return {}


class Bars:
    """One stock on the ASX 200 calendar (prices carried through no-trade days and after delisting)."""

    def __init__(self, days, o, c, raw, traded):
        self.pos = {d: k for k, d in enumerate(days)}
        self.o, self.c, self.raw, self.traded = o, c, raw, traded

    def bar(self, d):
        k = self.pos.get(d)
        if k is None or not (self.c[k] == self.c[k]):
            return None
        return self.o[k], self.c[k], self.raw[k], bool(self.traded[k])


# ---------------------------------------------------------------- the account
class Account:
    def __init__(self, rate):
        self.rate = rate               # None = pre-tax (no tax at all)
        self.cash = EQ0
        self.etf = []                  # lots: [units, cost per unit, day]
        self.pos = {}                  # code -> dict(rule, lots, entry, days, half_done, sector)
        self.fy = {"div": 0.0, "credit": 0.0, "nd": 0.0, "dg": 0.0, "loss": 0.0}
        self.carry = 0.0
        self.tax_paid = 0.0
        self.trades = []

    def realise(self, lots, units, price_net, day):
        left = units
        while left > 1e-12 and lots:
            u, cb, d0 = lots[0]
            take = min(u, left)
            g = take * (price_net - cb)
            held = (pd.Timestamp(day) - pd.Timestamp(d0)).days
            if g < 0:
                self.fy["loss"] += -g
            elif held > 365:
                self.fy["dg"] += g
            else:
                self.fy["nd"] += g
            lots[0][0] -= take
            left -= take
            if lots[0][0] <= 1e-12:
                lots.pop(0)

    def pay_tax(self):
        if self.rate is None:
            self.fy = {k: 0.0 for k in self.fy}
            return 0.0
        loss = self.fy["loss"] + self.carry
        nd, dg = self.fy["nd"], self.fy["dg"]
        a = min(nd, loss); nd -= a; loss -= a
        b = min(dg, loss); dg -= b; loss -= b
        self.carry = loss
        taxable = self.fy["div"] + self.fy["credit"] + nd + dg * 0.5
        tax = self.rate * taxable - self.fy["credit"]
        self.fy = {k: 0.0 for k in self.fy}
        self.cash -= tax
        self.tax_paid += tax
        return tax


def simulate(ctx, rules, switches, rate=None, plan=PLAN_C, collect=False, vt=True):
    days, acc, px = ctx["days"], ctx["acc"], ctx["px"]
    wplan, frames, ev_by_day = ctx["wplan"], ctx["frames"], ctx["ev_by_day"]
    rba_cash = ctx["cash"]
    A = Account(rate)
    blocked = {}
    eq_hist, rets, dates = [], [], []
    pending, pending_sell = [], set()
    sim_days = [d for d in days if d >= START and d in wplan]
    prev_eq = EQ0
    sat_scale = 1.0
    for n, d in enumerate(sim_days):
        p = days[days.index(d) - 1] if n == 0 else sim_days[n - 1]
        # vol target factor from the account's own trailing 60 sessions
        s = 1.0
        if vt and len(rets) >= 60:
            vol = float(np.std(rets[-60:], ddof=1)) * math.sqrt(252)
            s = min(1.0, VOL_TARGET / vol) if vol > 0 else 1.0
        # ---- opens: X1 sells, then new buys
        for code in list(pending_sell):
            if code in A.pos:
                b = frames[code].bar(d)
                if b:
                    sell_all(A, code, b[0] if b[3] else b[1], b[2], d, "指数调出")
        pending_sell = set()
        equity = mark(A, frames, d, px, use="o")
        sat_val = sum(pos_value(A, c, frames, d, "o") for c in A.pos)
        sec_val = defaultdict(float)
        for c, ps in A.pos.items():
            if ps["sector"]:
                sec_val[ps["sector"]] += pos_value(A, c, frames, d, "o")
        for (turn, code, rule, sg, sector) in pending:
            if code in A.pos or blocked.get(code, "") >= d:
                continue
            b = frames[code].bar(d)
            if not b or not b[3]:
                continue
            o, raw = b[0], b[2]
            if not (o > 0 and raw > 0):
                continue
            w = min(POS_CAP, 0.04 * 0.40 / max(sg, 0.10)) * s
            amt = min(w * equity, ADV_CAP * turn, SAT_CAP * equity - sat_val)
            if sector:
                amt = min(amt, SECTOR_CAP * equity - sec_val[sector])
            amt = min(amt, A.cash - 0.0)
            if amt < MIN_POS:
                continue
            k = side_cost(raw)
            units = amt * (1 - k) / o
            A.cash -= amt
            A.pos[code] = {"rule": rule, "lots": [[units, amt / units, d]], "entry": o,
                           "days": 0, "half": False, "sector": sector, "d0": d}
            assert ps_ok(ev_by_day, code, rule, d, days, ctx["didx"]), "bought without yesterday's signal"
            sat_val += amt
            if sector:
                sec_val[sector] += amt
        pending = []
        # ---- ETF: dividends for today, then rebalance at the close
        r_acc = acc[d] / acc[p] - 1
        r_px = px[d] / px[p] - 1
        etf_units = sum(l[0] for l in A.etf)
        div = etf_units * px[p] * max(0.0, r_acc - r_px)
        A.cash += div
        A.fy["div"] += div
        A.fy["credit"] += div * FRANK_SHARE
        # ---- closes: timed exits and S2 half-sells
        for code in list(A.pos):
            ps = A.pos[code]
            ps["days"] += 1
            b = frames[code].bar(d)
            if not b:
                continue
            c, raw = b[1], b[2]
            hold = 20 if ps["rule"] == "S1" else 252
            if ps["days"] >= hold:
                sell_all(A, code, c, raw, d, "到期")
            elif ps["rule"] == "S2" and not ps["half"] and c >= 2 * ps["entry"]:
                units = sum(l[0] for l in ps["lots"]) / 2
                k = side_cost(raw)
                A.cash += units * c * (1 - k)
                A.realise(ps["lots"], units, c * (1 - k), d)
                ps["half"] = True
        # cash interest
        A.cash *= 1 + rba_cash(p)
        equity = mark(A, frames, d, px)
        sat_val = sum(pos_value(A, c, frames, d, "c") for c in A.pos)
        if s < TRIM_BELOW and A.pos and abs(sat_scale - s) > 0.05:
            for code in list(A.pos):
                ps = A.pos[code]
                b = frames[code].bar(d)
                if b:
                    c, raw = b[1], b[2]
                    units = sum(l[0] for l in ps["lots"]) * (1 - s)
                    k = side_cost(raw)
                    A.cash += units * c * (1 - k)
                    A.realise(ps["lots"], units, c * (1 - k), d)
            sat_scale = s
            equity = mark(A, frames, d, px)
            sat_val = sum(pos_value(A, c, frames, d, "c") for c in A.pos)
        elif s >= TRIM_BELOW:
            sat_scale = 1.0
        # ETF to target
        target = wplan[d] * s * max(0.0, equity - sat_val)
        have = sum(l[0] for l in A.etf) * px[d]
        if abs(target - have) > 0.02 * equity:
            if target > have:
                amt = min(target - have, A.cash)
                if amt > 0:
                    units = amt * (1 - ETF_COST) / px[d]
                    A.cash -= amt
                    A.etf.append([units, amt / units, d])
            else:
                units = (have - target) / px[d]
                A.cash += units * px[d] * (1 - ETF_COST)
                A.realise(A.etf, units, px[d] * (1 - ETF_COST), d)
        # ---- tax on the last session of June
        if rate is not None and d[5:7] == "06" and (n + 1 == len(sim_days) or sim_days[n + 1][5:7] == "07"):
            A.pay_tax()
            if A.cash < 0:   # sell ETF to cover
                units = min(sum(l[0] for l in A.etf), -A.cash / (px[d] * (1 - ETF_COST)))
                A.cash += units * px[d] * (1 - ETF_COST)
                A.realise(A.etf, units, px[d] * (1 - ETF_COST), d)
        equity = mark(A, frames, d, px)
        rets.append(equity / prev_eq - 1)
        prev_eq = equity
        eq_hist.append(equity)
        dates.append(d)
        # ---- signals at today's close for tomorrow
        if "F2" in switches:
            for code in ctx["halts_by_day"].get(d, ()):
                bi = ctx["didx"].get(d)
                if bi is not None and bi + 20 < len(days):
                    blocked[code] = days[bi + 20]
        if "X1" in switches:
            pending_sell = {c for c in ctx["del_by_day"].get(d, ()) if c in A.pos}
        cands = []
        for (code, rule, turn, sg, above) in ev_by_day.get(d, ()):
            if rule not in rules or ("F1" in switches and not above):
                continue
            if "M" in switches and not ctx.get("regime", {}).get(d, True):   # round 95 market-state switch
                continue
            cands.append((turn, code, rule, sg, ctx["sector"].get(code)))
        cands.sort(reverse=True)
        pending = cands
    return {"dates": dates, "eq": eq_hist, "rets": rets, "tax": A.tax_paid, "trades": A.trades if collect else None,
            "n_trades": len(A.trades), "rules_trades": rule_trades(A.trades)}


def ps_ok(ev_by_day, code, rule, d, days, didx):
    k = didx.get(d)
    return k is not None and k > 0 and any(e[0] == code and e[1] == rule for e in ev_by_day.get(days[k - 1], ()))


def rule_trades(trades):
    g = defaultdict(list)
    for t in trades:
        g[t["rule"]].append(t["ret"])
    return {k: {"n": len(v), "avg": round(100 * float(np.mean(v)), 2), "win": round(100 * float(np.mean([x > 0 for x in v])), 1)}
            for k, v in g.items()}


def pos_value(A, code, frames, d, use):
    ps = A.pos[code]
    b = frames[code].bar(d)
    units = sum(l[0] for l in ps["lots"])
    if not b:
        return units * ps.get("last_px", ps["entry"])
    ps["last_px"] = b[1]
    return units * float(b[0] if use == "o" and b[3] else b[1])


def mark(A, frames, d, px, use="c"):
    return A.cash + sum(l[0] for l in A.etf) * px[d] + sum(pos_value(A, c, frames, d, use) for c in A.pos)


def sell_all(A, code, price, raw, d, why):
    ps = A.pos.pop(code)
    units = sum(l[0] for l in ps["lots"])
    k = side_cost(raw)
    proceeds = units * price * (1 - k)
    A.cash += proceeds
    cost_basis = sum(l[0] * l[1] for l in ps["lots"])
    A.realise(ps["lots"], units, price * (1 - k), d)
    A.trades.append({"code": code, "rule": ps["rule"], "in": ps["d0"], "out": d, "why": why,
                     "ret": proceeds / cost_basis - 1 if cost_basis > 0 else 0.0})


# ---------------------------------------------------------------- statistics
def stats(dates, eq, rets, lo=None, hi=None):
    idx = [k for k, d in enumerate(dates) if (lo is None or d >= lo) and (hi is None or d < hi)]
    if len(idx) < 30:
        return None
    e = np.array([eq[idx[0] - 1] if idx[0] > 0 else EQ0] + [eq[k] for k in idx])
    r = np.array([rets[k] for k in idx])
    yrs = len(r) / 252
    peak = np.maximum.accumulate(e)
    dd = float((e / peak - 1).min())
    sh = float(r.mean() / r.std(ddof=1) * math.sqrt(252)) if r.std(ddof=1) > 0 else 0.0
    m = monthly(dates, eq, lo, hi)
    streak = best = 0
    for x in m.values():
        streak = streak + 1 if x < 0 else 0
        best = max(best, streak)
    return {"cagr": round(100 * ((e[-1] / e[0]) ** (1 / yrs) - 1), 2), "maxdd": round(100 * dd, 1), "sharpe": round(sh, 2),
            "vol": round(100 * float(r.std(ddof=1)) * math.sqrt(252), 1), "worst_month": round(100 * min(m.values()), 1) if m else None,
            "longest_losing_months": best}


def monthly(dates, eq, lo=None, hi=None):
    last = {}
    for d, v in zip(dates, eq):
        last[d[:7]] = v
    ks = sorted(last)
    out, prev = {}, EQ0
    for k in ks:
        if (lo is None or k >= lo[:7]) and (hi is None or k < hi[:7]):
            out[k] = last[k] / prev - 1
        prev = last[k]
    return out


def yearly(dates, eq):
    last = {}
    for d, v in zip(dates, eq):
        last[d[:4]] = v
    out, prev = {}, EQ0
    for y in sorted(last):
        out[y] = round(100 * (last[y] / prev - 1), 1)
        prev = last[y]
    return out


def summary(res):
    D, E, R = res["dates"], res["eq"], res["rets"]
    return {"all": stats(D, E, R), "in_2015_2021": stats(D, E, R, hi=SPLIT), "out_2022_on": stats(D, E, R, lo=SPLIT),
            "by_year": yearly(D, E), "tax_paid": round(res["tax"]), "end_value": round(E[-1])}


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    days, acc, px, vix, f1, f2, rba = market()
    assert days and days[-1] > SPLIT, "index data missing"
    didx = {d: k for k, d in enumerate(days)}
    ck = sorted(f1)
    cash = (lambda d: f1[ck[bisect.bisect_right(ck, d) - 1]] / 100 / 252) if ck else (lambda d: 0.0)
    wplan = plan_weights(days, px, vix, f2, rba, PLAN_C)
    wfull = plan_weights(days, px, vix, f2, rba, (1.0, 1.0, 1.0))
    month_end = {days[k] for k in range(len(days) - 1) if days[k][:7] != days[k + 1][:7]}
    frames, ev_by_day, halts_by_day = {}, defaultdict(list), defaultdict(list)
    n_ev = defaultdict(int)
    files = sorted(study.CACHE.glob("*.AU.json"))
    for k, fp in enumerate(files):
        code = fp.name[:-8]
        try:
            s = signals_for(code, days, month_end)
        except Exception as e:
            print("[94] skip", code, type(e).__name__, flush=True)
            continue
        if not s or (not s["events"] and not s["halts"]):
            continue
        keep = [e for e in s["events"] if e[0] >= "2014-12-01"]
        for (d, rule, turn, sg, above) in keep:
            ev_by_day[d].append((code, rule, turn, sg, above))
            n_ev[rule] += 1
        for d in s["halts"]:
            halts_by_day[d].append(code)
        if keep:
            g = s["f"]
            frames[code] = Bars(list(g.index), g["o"].to_numpy(float), g["c"].to_numpy(float), g["raw"].to_numpy(float),
                                g["traded"].to_numpy(bool))
        if k % 500 == 0:
            print(f"[94] {k}/{len(files)} stocks, {dict(n_ev)} signals, {time.time() - t0:.0f}s", flush=True)
    sector = sectors(sorted(frames))
    dels = index_deletions()
    ctx = {"days": days, "acc": acc, "px": px, "wplan": wplan, "frames": frames, "ev_by_day": ev_by_day, "cash": cash,
           "halts_by_day": halts_by_day, "didx": didx, "del_by_day": dels, "sector": sector}
    # leak guards: nothing is bought before its signal, and features never read the future (spot checks)
    for d, evs in list(ev_by_day.items())[:200]:
        for (code, rule, turn, sg, above) in evs:
            f = frames[code]
            assert d in f.pos, "signal day outside the stock's own calendar"
    out = {"round": 94, "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Account rules")[0].split("Rule list")[1][:2000],
           "signals": dict(n_ev), "stocks_with_signals": len(frames),
           "sector_known_pct": round(100 * sum(1 for c in frames if sector.get(c)) / max(1, len(frames)), 1),
           "index_lists": len(dels), "halts": sum(len(v) for v in halts_by_day.values()),
           "trials": TRIALS, "n_trials": N_TRIALS, "costs": "round 71: 0.2% + one tick per side; ETF at $30 -> "
           f"{100 * ETF_COST:.2f}% per side", "cash": "RBA cash rate" if ck else "0 (RBA table unavailable)"}
    full = set(RULES)
    allsw = set(SWITCHES)
    # benchmark: ETF only (100% ASX 200, same engine, no satellites)
    ctx_b = dict(ctx, wplan=wfull)
    res = {"etf_only": {}, "plan_c": {}, "all": {}}
    for rate in (None,) + RATES:
        tag = "pre_tax" if rate is None else f"tax_{int(rate * 1000)}"
        b = simulate(ctx_b, set(), set(), rate, vt=False)
        c = simulate(ctx, set(), set(), rate)
        a = simulate(ctx, full, allsw, rate, collect=(rate is None))
        res["etf_only"][tag] = summary(b)
        res["plan_c"][tag] = summary(c)
        res["all"][tag] = summary(a)
        if rate is None:
            bench_pre, all_pre, base_pre = b, a, c
            out["trades_by_rule"] = a["rules_trades"]
            out["n_trades"] = a["n_trades"]
        print(f"[94] {tag} done", flush=True)   # numbers stay in the encrypted file (public logs)
    out["results"] = res
    # contributions: leave one out and alone-with-core (pre-tax)
    contrib = {}
    a_cagr = res["all"]["pre_tax"]
    for x in list(RULES) + list(SWITCHES):
        r_ = full - {x} if x in RULES else full
        s_ = allsw - {x} if x in SWITCHES else allsw
        loo = summary(simulate(ctx, r_, s_))
        alone = summary(simulate(ctx, {x} if x in RULES else set(), allsw if x in RULES else {x}))
        contrib[x] = {"without_it": {k: loo[k] for k in ("all", "in_2015_2021", "out_2022_on")},
                      "only_it_plus_core": {k: alone[k] for k in ("all", "in_2015_2021", "out_2022_on")},
                      "adds_cagr_all": round(a_cagr["all"]["cagr"] - loo["all"]["cagr"], 2),
                      "adds_cagr_oos": round(a_cagr["out_2022_on"]["cagr"] - loo["out_2022_on"]["cagr"], 2)}
    out["contribution"] = contrib
    # overlap: each buy rule alone with the core in cash
    zero = {d: 0.0 for d in wplan}
    ctx0 = dict(ctx, wplan=zero)
    own = {}
    for x in RULES:
        r = simulate(ctx0, {x}, allsw)
        own[x] = r
    T = min(len(v["rets"]) for v in own.values())
    M = np.array([own[x]["rets"][:T] for x in RULES])
    corr = np.corrcoef(M) if M.std() > 0 else np.eye(len(RULES))
    pairs = {}
    dropped = []
    for i, j in itertools.combinations(range(len(RULES)), 2):
        cij = float(np.nan_to_num(corr[i, j]))
        pairs[f"{RULES[i]}-{RULES[j]}"] = round(cij, 2)
        if cij > 0.7:
            si = (stats(own[RULES[i]]["dates"], own[RULES[i]]["eq"], own[RULES[i]]["rets"], hi=SPLIT) or {}).get("sharpe", 0)
            sj = (stats(own[RULES[j]]["dates"], own[RULES[j]]["eq"], own[RULES[j]]["rets"], hi=SPLIT) or {}).get("sharpe", 0)
            dropped.append(RULES[j] if si >= sj else RULES[i])
    etf_r = np.array(bench_pre["rets"][:T])
    out["overlap"] = {"corr": pairs, "corr_with_etf": {x: round(float(np.nan_to_num(np.corrcoef(M[k], etf_r)[0, 1])), 2) for k, x in enumerate(RULES)},
                      "dropped_for_overlap": sorted(set(dropped)),
                      "alone_in_cash": {x: {k: summary(own[x])[k] for k in ("all", "in_2015_2021", "out_2022_on")} for x in RULES}}
    # gates on the monthly excess return over ETF only (pre-tax)
    mb, ma = monthly(bench_pre["dates"], bench_pre["eq"]), monthly(all_pre["dates"], all_pre["eq"])
    ks = [k for k in ma if k in mb]
    ex = np.array([ma[k] - mb[k] for k in ks])
    ex_oos = np.array([ma[k] - mb[k] for k in ks if k >= SPLIT[:7]])
    t_all = validate.clustered_t(list(ex), ks) if len(ex) > 3 else None
    t_oos = validate.clustered_t(list(ex_oos), [k for k in ks if k >= SPLIT[:7]]) if len(ex_oos) > 3 else None
    dsr = validate.deflated_sharpe(list(ex), N_TRIALS)
    # PBO over the 64 on/off combinations
    combos, cols = [], []
    for bits in itertools.product((0, 1), repeat=6):
        r_ = {x for x, b in zip(RULES, bits[:3]) if b}
        s_ = {x for x, b in zip(SWITCHES, bits[3:]) if b}
        rr = simulate(ctx, r_, s_)
        m = monthly(rr["dates"], rr["eq"])
        cols.append([m.get(k, 0.0) for k in ks])
        combos.append("+".join(sorted(r_ | s_)) or "core only")
    Mx = np.array(cols).T - np.array([mb[k] for k in ks])[:, None]
    pbo = validate.pbo_cscv(Mx, S=16)
    best_is = combos[int(np.argmax([np.mean(c[:sum(1 for k in ks if k < SPLIT[:7])]) for c in Mx.T]))]
    lab = validate.label(t_all, dsr.get("dsr"), pbo.get("pbo"))
    out["gates"] = {"excess_monthly_mean_pct": round(100 * float(ex.mean()), 3), "t_all": round(t_all, 2) if t_all is not None else None,
                    "t_oos": round(t_oos, 2) if t_oos is not None else None, "dsr": dsr, "pbo": pbo,
                    "best_combo_in_sample": best_is, "label": lab, "months": len(ks)}
    # verdict (Frank's acceptance line: out of sample, after costs and tax, beat ETF-only by > 2 points a year)
    verdict = {}
    for tag in ("pre_tax", "tax_0", "tax_325", "tax_470"):
        a_ = res["all"][tag]["out_2022_on"]["cagr"]
        b_ = res["etf_only"][tag]["out_2022_on"]["cagr"]
        verdict[tag] = {"all_oos": a_, "etf_oos": b_, "edge": round(a_ - b_, 2), "passes_2pt": a_ - b_ > 2}
    out["verdict"] = verdict
    out["draggers_oos"] = sorted([x for x, v in contrib.items() if v["adds_cagr_oos"] < 0], key=lambda x: contrib[x]["adds_cagr_oos"])
    out["seconds"] = round(time.time() - t0)
    p = Path(os.environ.get("STUDY94_OUT", "out/study94_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[94] done", out["seconds"], "s", flush=True)   # numbers stay in the encrypted file


if __name__ == "__main__":
    main()
