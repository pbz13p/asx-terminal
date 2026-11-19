"""Round 87: test the 云聪 (YouTube) trading rules on ASX, A-shares and Binance perps. Rules fixed BEFORE seeing results.

Source: 163 full transcripts read (4 by Claude, 159 by DeepSeek, notes in research/creators/云聪/). Only rules with a
clear mechanical version are tested; daily bars only for stocks (no minute data), 1-hour bars for crypto intraday.

Markets / universe (every stock incl. delisted; signal day i uses data up to its close; entry at the NEXT open):
  ASX  data/eod *.AU  (round-80 consolidation fix), 20-day median raw $ traded >= $20k, raw price >= 2c, >= 60 days listed.
  A股  data/cn        20-day median value >= 10M CNY; cannot buy at a limit-up open, cannot sell at a limit-down
                      (10%, 20% for 300/301/688/689); long only.
  加密 data/crypto    daily bars = UTC days from 1h klines; 20-day median quote volume >= 5M USDT, >= 30 days listed;
                      long and short; funding paid/received over the holding days.
Costs per side: ASX round 71 (0.2% + one tick / price); A股 0.15% (+0.05% stamp on the sell); 加密 0.07% (taker + slip).

Rules (long unless noted):
  R0  随便买            every eligible stock-day, hold 1/5/20 days (the baseline everything is compared with)
  R1  涨的还会涨         day return in the top 10% of the market that day (and > 0); hold 1/5/20
  R3  放量突破           close > prior 20-day high and volume > 1.5x 20-day average; exits:
        a 固定20天 / b 移动止损(收盘破10日最低收盘, 初始止损2ATR, 最长120天) / c 333分批(+10%,+20%卖1/3, 余下按b)
        d 巨量套现(成交量>=2倍均量且60日最大 → 当天收盘走, 否则按b) / e 时间止损(第3天收盘没赚就走, 否则按a)
  R3h/R3c  R3a only on "赚钱效应" days (share of stocks up >= 5% (加密 10%) above its own past-250-day 80th pct) vs other days
  R4  横有多长竖有多高   R3a split by days since the close was last above today's close: <=40 / 41-80 / 81-160 / >160
  R5  2B/破底翻/弹簧     low < prior 20-day low, close back above it, volume >= 20-day average; stop at the signal low,
                      hold 10 days; 加密 also the mirror (2B top, short)
  R6  威科夫二次测试      SC = 20-day new low on volume >= 3x 60-day average; ST = 5-30 days later, low within 3% of the SC
                      low, volume <= half the SC volume, close >= SC low → buy; stop 1% under SC low; hold 20 days
  R7  拉里波动突破        trigger = open + 1.8 x mean(high-open) of the last 4 days; buy when the high reaches it (at the
                      open if it opens above), sell next open; R7n = only after a narrow day (range < 0.75 ATR20)
  R10 攻击日             yesterday upper shadow >= 2x body and >= 0.5 ATR, today close > yesterday high; hold 3 days
  R14 低开就砍(养家/退学)  close > prior 60-day high close, volume >= 1.5x, day return >= 5%, not limit-up → buy at the
                      CLOSE; next day open below cost → sell at the open; next day red → sell at that close; else sell
                      at the 3rd close. R14ctl = same entries, always sell at the 3rd close.
  R8  开盘价分水线(加密1小时) UTC day open; trend = yesterday's close vs 20-day average; at 04:00 if the last close is
                      above the day open (uptrend) → long, stop at the day open, else exit at 24:00; mirror short.
Split: in sample = signal before 2022 (加密 2024), out of sample after. Verdict "有效" only if OOS expectancy beats R0
(same hold) with month-clustered t > 2, >= 100 OOS trades, and the in-sample sign agrees.
Reported per rule/market: n, expectancy (net %), hit %, average win / loss, win:loss ratio, biggest winner's share of
gains, longest losing streak, excess over the equal-weight market over the same days, t (clustered by month).
"""
import gzip
import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

import study
import study80

OUT = Path(os.environ.get("STUDY87_OUT", "out/study87_results.json"))
SPLIT = {"ASX": "2022-01-01", "A股": "2022-01-01", "加密": "2024-01-01"}
T = defaultdict(list)          # (market, rule) -> [(date, net, excess)]


# ------------------------------------------------------------------ loading
def asx_stocks():
    for f in sorted(study.CACHE.glob("*.AU.json")):
        rows = study.load(f.name[:-5])
        if len(rows) < 80:
            continue
        rows = [r + (r[6] * r[5],) for r in rows]
        rows, _ = study80.fix_consolidations(rows)
        d = [r[0] for r in rows]
        a = np.array([[r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in rows], dtype=float)
        yield f.name[:-8], {"d": d, "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": a[:, 4], "raw": a[:, 5], "val": a[:, 6]}


def cn_stocks():
    for f in sorted(Path("data/cn").glob("*.json.gz")):
        rows = [r for r in json.loads(gzip.decompress(f.read_bytes())) if r[4] and r[5] and r[4] > 0 and r[5] > 0]
        if len(rows) < 80:
            continue
        d = [r[0] for r in rows]
        a = np.array([[r[1] or r[4], r[2] or r[4], r[3] or r[4], r[4], r[5], r[6] or 0] for r in rows], dtype=float)
        k = a[:, 4] / a[:, 3]
        code = f.name.split(".")[0]
        yield code, {"d": d, "o": a[:, 0] * k, "h": a[:, 1] * k, "l": a[:, 2] * k, "c": a[:, 4], "v": a[:, 5], "raw": a[:, 3],
                     "rawo": a[:, 0], "val": a[:, 3] * a[:, 5], "lim": 0.2 if code[:3] in ("300", "301", "688", "689") else 0.1}


def crypto_coins():
    for f in sorted(Path("data/crypto").glob("*_1h.json")):
        K = json.loads(f.read_text())
        if len(K) < 24 * 60:
            continue
        sym = f.name[:-8]
        F = json.loads((f.parent / f"{sym}_funding.json").read_text()) if (f.parent / f"{sym}_funding.json").exists() else []
        days = defaultdict(list)
        for t, o, h, l, c, qv in K:
            days[time.strftime("%Y-%m-%d", time.gmtime(t))].append((o, h, l, c, qv))
        fund = defaultdict(float)
        for t, r in F:
            fund[time.strftime("%Y-%m-%d", time.gmtime(t))] += r
        d = sorted(k for k, v in days.items() if len(v) >= 20)
        a = np.array([[days[x][0][0], max(z[1] for z in days[x]), min(z[2] for z in days[x]), days[x][-1][3],
                       sum(z[4] for z in days[x])] for x in d], dtype=float)
        yield sym, {"d": d, "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": a[:, 4], "raw": a[:, 3], "val": a[:, 4],
                    "fund": np.array([fund.get(x, 0.0) for x in d]), "K": K, "F": F}


LOADERS = {"ASX": asx_stocks, "A股": cn_stocks, "加密": crypto_coins}


# ------------------------------------------------------------------ helpers
def roll(x, n, fn):
    out = np.full(len(x), np.nan)
    for i in range(n, len(x) + 1):
        out[i - 1] = fn(x[i - n:i])
    return out


def prep(s, market):
    c, h, l, o, v = s["c"], s["h"], s["l"], s["o"], s["v"]
    n = len(c)
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    s["atr"] = np.r_[np.nan, roll(tr, 20, np.mean)[:-1]]              # ATR of the 20 days BEFORE each day
    s["vav"] = np.r_[np.nan, roll(v, 20, np.mean)[:-1]]               # 20-day average volume before each day
    s["vav60"] = np.r_[np.nan, roll(v, 60, np.mean)[:-1]]
    s["hi20"] = np.r_[np.nan, roll(h, 20, np.max)[:-1]]
    s["lo20"] = np.r_[np.nan, roll(l, 20, np.min)[:-1]]
    s["hc60"] = np.r_[np.nan, roll(c, 60, np.max)[:-1]]
    s["ma20"] = roll(c, 20, np.mean)
    s["med"] = roll(s["val"], 20, np.median)
    s["ret"] = c / pc - 1
    floor = {"ASX": 2e4, "A股": 1e7, "加密": 5e6}[market]
    age = 30 if market == "加密" else 60
    ok = (s["med"] >= floor) & (np.arange(n) >= age) & (v > 0)
    if market == "ASX":
        ok &= s["raw"] >= 0.02
    s["ok"] = ok
    if market == "A股":
        prc = np.r_[np.nan, s["raw"][:-1]]
        s["limup_close"] = s["raw"] >= np.round(prc * (1 + s["lim"]), 2) - 0.005
        s["limdn_close"] = s["raw"] <= np.round(prc * (1 - s["lim"]), 2) + 0.005
        s["limup_open"] = s["rawo"] >= np.round(prc * (1 + s["lim"]), 2) - 0.005
        s["limdn_open"] = s["rawo"] <= np.round(prc * (1 - s["lim"]), 2) + 0.005
    return s


def cost(market, price):
    if market == "ASX":
        return 0.002 + ((0.001 if price < 0.10 else 0.005 if price < 2 else 0.01) / price)
    return 0.0015 if market == "A股" else 0.0007


def net_of(market, s, side, ep, xp, ei, xi):
    g = side * (xp / ep - 1)
    sell_extra = 0.0005 if market == "A股" else 0.0
    pin = s["raw"][ei] if market != "加密" else ep
    pout = s["raw"][xi] if market != "加密" else xp
    n = (1 + g) * (1 - cost(market, pin)) * (1 - cost(market, pout) - sell_extra) - 1
    if market == "加密":
        n -= side * float(np.sum(s["fund"][ei:xi]))   # longs pay positive funding; shorts receive it
    return n


def a_sell_index(s, k, at_open):
    """A股: a sell at a limit-down open/close slips to the next day's open until it is possible."""
    if "limdn_open" not in s:
        return k, at_open
    n = len(s["c"])
    if (at_open and s["limdn_open"][k]) or (not at_open and s["limdn_close"][k]):
        k += 1
        while k < n - 1 and s["limdn_open"][k]:
            k += 1
        return min(k, n - 1), True
    return k, at_open


def run(s, e, hold, side=1, stop=None, trail=False, tstop=None, volx=False, thirds=False, maxhold=120):
    """Enter at the open of day e; returns list of (exit index, exit price, weight)."""
    o, h, l, c, v = s["o"], s["h"], s["l"], s["c"], s["v"]
    n = len(c)
    ep = o[e]
    last = min(n - 1, e + (maxhold if (trail or volx) else hold) - 1)
    if stop is None and not (trail or tstop or volx or thirds):
        return [(last, c[last], 1.0)]
    parts, w_left = [], 1.0
    tgt = [ep * 1.10, ep * 1.20] if thirds else []
    for k in range(e, last + 1):
        if stop is not None and side * (l[k] if side > 0 else h[k]) <= side * stop:
            px = min(o[k], stop) if side > 0 else max(o[k], stop)
            parts.append((k, px, w_left))
            return parts
        while tgt and h[k] >= tgt[0]:
            parts.append((k, max(o[k], tgt[0]), 1 / 3))
            w_left -= 1 / 3
            tgt.pop(0)
        if tstop and k == e + tstop - 1 and side * (c[k] - ep) <= 0:
            parts.append((k, c[k], w_left))
            return parts
        if volx and k > e and v[k] >= 2 * s["vav"][k] and v[k] >= np.max(v[max(0, k - 59):k + 1]):
            parts.append((k, c[k], w_left))
            return parts
        if trail and k >= e + 1 and k >= 10:
            lo10 = np.min(c[k - 10:k]) if side > 0 else np.max(c[k - 10:k])
            if side * (c[k] - lo10) < 0:
                parts.append((k, c[k], w_left))
                return parts
    parts.append((last, c[last], w_left))
    return parts


def book(market, rule, s, e, parts, side=1, entry_price=None):
    """Entry at the open of day e (or at entry_price on day e-1's close / intraday); signal date = day e-1."""
    ep = entry_price if entry_price is not None else s["o"][e]
    ei = e - 1 if entry_price is not None else e
    tot, k_last = 0.0, e
    for k, px, w in parts:
        if market == "A股":
            k2, _ = a_sell_index(s, k, px == s["o"][k])
            if k2 != k:
                px = s["o"][k2]
            k = k2
        tot += w * net_of(market, s, side, ep, px, ei, k)
        k_last = max(k_last, k)
    T[(market, rule)].append((s["d"][e - 1], tot, s["d"][e - 1], s["d"][k_last]))


# ------------------------------------------------------------------ pass 1: market-wide day stats
def pass1(market):
    by_date = defaultdict(list)
    for code, s in LOADERS[market]():
        s = prep(s, market)
        for i in np.nonzero(s["ok"] & np.isfinite(s["ret"]))[0]:
            by_date[s["d"][i]].append(float(s["ret"][i]))
    q90, hot, idx = {}, {}, {}
    big = 0.10 if market == "加密" else 0.05
    level, hist = 1.0, []
    for d in sorted(by_date):
        r = np.array(by_date[d])
        r = r[np.abs(r) < 3]
        if len(r) < 20:
            continue
        q90[d] = float(np.quantile(r, 0.9))
        b = float(np.mean(r >= big))
        hot[d] = len(hist) >= 120 and b > np.quantile(hist[-250:], 0.8)
        hist.append(b)
        level *= 1 + float(np.mean(np.clip(r, -0.9, 3)))
        idx[d] = level
    return q90, hot, idx


# ------------------------------------------------------------------ pass 2: signals
def pass2(market, q90, hot):
    n_st = 0
    for code, s in LOADERS[market]():
        s = prep(s, market)
        n_st += 1
        c, o, h, l, v = s["c"], s["o"], s["h"], s["l"], s["v"]
        n = len(c)
        I = np.arange(n)
        base = s["ok"] & np.isfinite(s["atr"]) & (s["atr"] > 0) & (I >= 60) & (I < n - 2)
        if market == "A股":
            base &= ~np.r_[s["limup_open"][1:], True]        # cannot buy at a limit-up open next day
        if not base.any():
            continue
        with np.errstate(invalid="ignore"):
            q = np.array([q90.get(x, np.inf) for x in s["d"]])
            m_r1 = base & (s["ret"] >= q) & (s["ret"] > 0)
            volup = v > 1.5 * s["vav"]
            m_up = base & (c > s["hi20"]) & volup
            m_dn = base & (c < s["lo20"]) & volup
            m_2b = base & (l < s["lo20"]) & (c > s["lo20"]) & (v >= s["vav"])
            m_2t = base & (h > s["hi20"]) & (c < s["hi20"]) & (v >= s["vav"])
            pb, pu = np.abs(np.r_[np.nan, (c - o)[:-1]]), np.r_[np.nan, (h - np.maximum(c, o))[:-1]]
            m_att = base & (pu >= 2 * np.maximum(pb, 1e-12)) & (pu >= 0.5 * s["atr"]) & (c > np.r_[np.nan, h[:-1]])
            m_r14 = base & (c > s["hc60"]) & (v >= 1.5 * s["vav"]) & (s["ret"] >= 0.05) & (I < n - 4)
            if market == "A股":
                m_r14 &= ~s["limup_close"]
            sc = (l < s["lo20"]) & (v >= 3 * s["vav60"]) & (I >= 61)
        for i in np.nonzero(base)[0][::5]:                     # R0: one day in five
            for H in (1, 5, 20):
                book(market, f"R0 随便买 {H}天", s, i + 1, run(s, i + 1, H))
        for i in np.nonzero(m_r1)[0]:
            for H in (1, 5, 20):
                book(market, f"R1 涨的还会涨 {H}天", s, i + 1, run(s, i + 1, H))
        for sd, mask in ((1, m_up), (-1, m_dn)):
            if sd < 0 and market != "加密":
                continue
            tag = "" if sd > 0 else " 做空"
            for i in np.nonzero(mask)[0]:
                e = i + 1
                st = o[e] - sd * 2 * s["atr"][i]
                book(market, "R3a 放量突破·固定20天" + tag, s, e, run(s, e, 20, sd), sd)
                book(market, "R3b 放量突破·移动止损" + tag, s, e, run(s, e, 120, sd, stop=st, trail=True), sd)
                if sd < 0:
                    continue
                book(market, "R3c 放量突破·333分批", s, e, run(s, e, 120, stop=st, trail=True, thirds=True))
                book(market, "R3d 放量突破·巨量套现", s, e, run(s, e, 120, stop=st, trail=True, volx=True))
                book(market, "R3e 放量突破·3天时间止损", s, e, run(s, e, 20, tstop=3))
                book(market, "R3h 突破·赚钱效应日" if hot.get(s["d"][i]) else "R3n 突破·其他日子", s, e, run(s, e, 20))
                j = i - 1
                while j > max(0, i - 250) and c[j] <= c[i]:
                    j -= 1
                days = i - 1 - j
                qt = "Q1 <=40天" if days <= 40 else "Q2 41-80天" if days <= 80 else "Q3 81-160天" if days <= 160 else "Q4 >160天"
                book(market, f"R4 盘整{qt}", s, e, run(s, e, 20))
        for sd, mask in ((1, m_2b), (-1, m_2t)):
            if sd < 0 and market != "加密":
                continue
            for i in np.nonzero(mask)[0]:
                book(market, "R5 2B破底翻" if sd > 0 else "R5 2B顶部 做空", s, i + 1,
                     run(s, i + 1, 10, sd, stop=(l[i] if sd > 0 else h[i])), sd)
        for j in np.nonzero(sc)[0]:                             # R6: first valid secondary test after each SC
            for i in range(j + 5, min(j + 31, n - 2)):
                if l[i] < l[j] * 0.97:
                    break
                if base[i] and abs(l[i] / l[j] - 1) <= 0.03 and v[i] <= 0.5 * v[j] and c[i] >= l[j]:
                    book(market, "R6 威科夫二次测试", s, i + 1, run(s, i + 1, 20, stop=l[j] * 0.99))
                    break
        for i in np.nonzero(m_att)[0]:
            book(market, "R10 攻击日 3天", s, i + 1, run(s, i + 1, 3))
        if market != "加密":
            for i in np.nonzero(m_r14)[0]:
                e, ep = i + 1, c[i]
                x3 = min(i + 3, n - 1)
                if o[e] < ep:
                    parts = [(e, o[e], 1.0)]
                elif c[e] < o[e]:
                    parts = [(e, c[e], 1.0)]
                else:
                    parts = [(x3, c[x3], 1.0)]
                book(market, "R14 低开就砍", s, e, parts, entry_price=ep)
                book(market, "R14ctl 同样买入·拿满3天", s, e, [(x3, c[x3], 1.0)], entry_price=ep)
        # R7: trigger on day e = i+1, bought intraday, sold at the next open
        hmo = h - o
        for i in np.nonzero(base & (I < n - 3))[0]:
            e = i + 1
            if market == "A股" and (s["limup_open"][e] or s["limup_close"][e]):
                continue
            trig = o[e] + 1.8 * float(np.mean(hmo[i - 3:i + 1]))
            if h[e] >= trig:
                ep = max(o[e], trig)
                book(market, "R7 拉里波动突破", s, e + 1, [(e + 1, o[e + 1], 1.0)], entry_price=ep)
                if (h[i] - l[i]) < 0.75 * s["atr"][i]:
                    book(market, "R7n 拉里突破·窄幅日后", s, e + 1, [(e + 1, o[e + 1], 1.0)], entry_price=ep)
        if market == "加密":
            r8(s)
        if n_st % 500 == 0:
            print(f"[87] {market} {n_st} done", flush=True)
    return n_st


def r8(s):
    """Opening-price divide on 1h bars (UTC days)."""
    K = s["K"]
    fund = {t: r for t, r in s["F"]}
    dclose = {s["d"][i]: (s["c"][i], s["ma20"][i], s["ok"][i]) for i in range(len(s["d"]))}
    days = defaultdict(list)
    for row in K:
        days[time.strftime("%Y-%m-%d", time.gmtime(row[0]))].append(row)
    keys = sorted(days)
    for a, b in zip(keys, keys[1:]):
        if a not in dclose or len(days[b]) < 24 or not dclose[a][2] or not np.isfinite(dclose[a][1]):
            continue
        H = days[b]
        dopen = H[0][1]
        side = 1 if dclose[a][0] > dclose[a][1] else -1
        if side * (H[3][4] - dopen) <= 0:
            continue
        ep, xp, t_in, t_out = H[4][1], H[-1][4], H[4][0], H[-1][0] + 3600
        for row in H[4:]:
            if side * ((row[3] if side > 0 else row[2]) - dopen) <= 0:
                xp, t_out = dopen, row[0] + 3600
                break
        g = side * (xp / ep - 1)
        net = (1 + g) * (1 - 0.0007) ** 2 - 1 - side * sum(r for t, r in fund.items() if t_in <= t < t_out)
        T[("加密", "R8 开盘价分水线(1小时)" + ("" if side > 0 else " 做空"))].append((b, net, b, b))


# ------------------------------------------------------------------ stats
def stats(rows, idx, split, hold_hint=None):
    if len(rows) < 30:
        return {"n": len(rows)}
    rows = sorted(rows)
    net = np.array([r[1] for r in rows])
    ex = []
    for d, x, d0, d1 in rows:
        i0, i1 = idx.get(d0), idx.get(d1)
        ex.append(x - (i1 / i0 - 1) if i0 and i1 else x)
    ex = np.array(ex)
    win, loss = net[net > 0], net[net <= 0]
    streak = best = 0
    for x in net:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    months = defaultdict(list)
    for (d, *_), x in zip(rows, net):
        months[d[:7]].append(x)
    mm = np.array([np.mean(v) for v in months.values()])
    t = float(mm.mean() / (mm.std(ddof=1) / math.sqrt(len(mm)))) if len(mm) > 2 and mm.std() > 0 else None
    out = {"n": int(len(net)), "期望%": round(100 * float(net.mean()), 2), "中位%": round(100 * float(np.median(net)), 2),
           "命中%": round(100 * float((net > 0).mean()), 1),
           "平均赚%": round(100 * float(win.mean()), 2) if len(win) else None,
           "平均亏%": round(100 * float(loss.mean()), 2) if len(loss) else None,
           "赚亏比": round(float(win.mean() / -loss.mean()), 2) if len(win) and len(loss) and loss.mean() < 0 else None,
           "最大赢家占总盈利%": round(100 * float(win.max() / win.sum()), 1) if len(win) else None,
           "最长连亏": int(best), "超额%": round(100 * float(ex.mean()), 2), "t(按月)": round(t, 2) if t is not None else None}
    return out


def main():
    t0 = time.time()
    res = {"method": __doc__.strip(), "markets": {}}
    for market in ("ASX", "A股", "加密"):
        if (market == "A股" and not Path("data/cn").exists()) or (market == "加密" and not Path("data/crypto").exists()):
            res["markets"][market] = {"skipped": "no data"}
            continue
        q90, hot, idx = pass1(market)
        n_st = pass2(market, q90, hot)
        split = SPLIT[market]
        R = {}
        for (m, rule), rows in sorted(T.items()):
            if m != market:
                continue
            R[rule] = {"样本内": stats([r for r in rows if r[0] < split], idx, split),
                       "样本外": stats([r for r in rows if r[0] >= split], idx, split),
                       "全部": stats(rows, idx, split)}
        res["markets"][market] = {"stocks": n_st, "split": split, "first": min(idx) if idx else None,
                                  "last": max(idx) if idx else None, "rules": R}
        print(f"[87] {market} finished, {n_st} instruments, {len(R)} rules, {time.time() - t0:.0f}s", flush=True)
        for k in [k for k in T if k[0] == market]:
            del T[k]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(f"[87] done {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
