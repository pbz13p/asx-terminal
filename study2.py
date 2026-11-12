"""Round 2: deeper, cleaner short-horizon study on tradable ASX stocks.

Fixes from round 1:
- Trading cost is estimated per stock from its own daily highs and lows
  (Corwin-Schultz spread, 20-day average) plus 0.1% commission, not guessed.
- The tradable universe is price >= $0.20 and 20-day median turnover >= $500k.
- Index opens from the data feed equal the prior close, so overnight effects
  are measured on real stock opens instead.
- Gross and net results both carry date-clustered t-statistics.

Writes data/study2_results.json (or STUDY2_OUT).
"""
import json
import math
import os
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import study

SPLIT = study.SPLIT
HOR = (1, 2, 5, 10)
MIN_PRICE, MIN_VALUE = float(os.environ.get("S2_MINP", .2)), float(os.environ.get("S2_MINV", 5e5))


class Agg:
    def __init__(self):
        self.d = defaultdict(lambda: [0.0, 0.0, 0, 0])

    def add(self, day, g, n):
        a = self.d[day]
        a[0] += g
        a[1] += n
        a[2] += 1
        a[3] += g > 0

    def _sum(self, lo=None, hi=None):
        items = [a for k, a in self.d.items() if (lo is None or k >= lo) and (hi is None or k < hi)]
        n = sum(a[2] for a in items)
        if n < 20:
            return None

        def t(idx):
            xs = [a[idx] / a[2] for a in items]
            if len(xs) < 3:
                return 0.0
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
            return m / (sd / math.sqrt(len(xs))) if sd else 0.0
        return {"n": n, "days": len(items), "gross": 100 * sum(a[0] for a in items) / n,
                "net": 100 * sum(a[1] for a in items) / n, "win": 100 * sum(a[3] for a in items) / n,
                "tg": t(0), "tn": t(1)}

    def summary(self):
        a = self._sum()
        if not a:
            return None
        return {"all": a, "in": self._sum(hi=SPLIT), "out": self._sum(lo=SPLIT)}


R = defaultdict(Agg)


def cs_spread(h0, l0, h1, l1):
    """Corwin-Schultz (2012) two-day high-low spread estimate."""
    if min(h0, l0, h1, l1) <= 0:
        return None
    beta = math.log(h0 / l0) ** 2 + math.log(h1 / l1) ** 2
    hh, ll = max(h0, h1), min(l0, l1)
    gamma = math.log(hh / ll) ** 2
    k = 3 - 2 * math.sqrt(2)
    alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
    s = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
    return max(0.0, s)


def median(a):
    b = sorted(a)
    n = len(b)
    return 0 if not n else (b[n // 2] if n % 2 else (b[n // 2 - 1] + b[n // 2]) / 2)


def add_fwd(key, rows, i, cost, entry_open=True):
    if i + 1 >= len(rows):
        return
    px = rows[i + 1][1] if entry_open else rows[i][4]
    if px <= 0 or (entry_open and rows[i + 1][5] <= 0):
        return
    for h in HOR:
        j = i + h
        if j >= len(rows):
            return
        g = rows[j][4] / px - 1
        if abs(g) > 1.5:
            return
        R[key + (h,)].add(rows[i][0], g, g - cost)


def us_returns(spx):
    out, dates = {}, []
    for k in range(1, len(spx)):
        out[spx[k][0]] = spx[k][4] / spx[k - 1][4] - 1
        dates.append(spx[k][0])
    return out, dates


def us_bucket(u):
    for lo, hi, n in ((-9, -.02, "美股跌≥2%"), (-.02, -.01, "美股跌1-2%"), (-.01, .01, "美股±1%内"),
                      (.01, .02, "美股涨1-2%"), (.02, 9, "美股涨≥2%")):
        if lo <= u < hi:
            return n


def prev_us(ud, d, cache={}):
    if d in cache:
        return cache[d]
    lo, hi = 0, len(ud)
    while lo < hi:
        m = (lo + hi) // 2
        if ud[m] < d:
            lo = m + 1
        else:
            hi = m
    cache[d] = ud[lo - 1] if lo else None
    return cache[d]


SPREADS = []
STREAK = defaultdict(Agg)


def scan(rows, xjo_ret, us_ret, us_dates):
    n = len(rows)
    if n < 300:
        return
    vals = [r[6] * r[5] for r in rows]
    sp = [None] * n
    for i in range(1, n):
        sp[i] = cs_spread(rows[i - 1][2], rows[i - 1][3], rows[i][2], rows[i][3])
    for i in range(25, n - 1):
        d = rows[i][0]
        if d < "2015-01-01" or rows[i][5] <= 0 or rows[i - 1][4] <= 0:
            continue
        price = rows[i][6]
        mv = median(vals[i - 20:i])
        if price < MIN_PRICE or mv < MIN_VALUE:
            continue
        s = [x for x in sp[i - 19:i + 1] if x is not None]
        spread = sum(s) / len(s) if s else .01
        tick = .005 if price < 2 else .01
        cost = study.COMMISSION + max(spread, tick / price)
        if i % 20 == 0:
            SPREADS.append(cost)
        size = "大票 额≥500万" if mv >= 5e6 else "中票 50-500万"
        r1 = rows[i][4] / rows[i - 1][4] - 1
        mkt = xjo_ret.get(d)
        vol_ratio = rows[i][5] / max(1, median([r[5] for r in rows[i - 20:i]]))
        # 1. big moves, split by volume and by whether the market moved too
        b = study.move_bucket(r1)
        if b:
            add_fwd(("A 单日大涨大跌后", b, size), rows, i, cost)
            add_fwd(("A 单日大涨大跌后", b, "全部可交易"), rows, i, cost)
            add_fwd(("B 大涨大跌+成交量", b + (" · 放量≥3倍" if vol_ratio >= 3 else " · 没放量"), "全部可交易"), rows, i, cost)
            if mkt is not None:
                tag = " · 大盘同向" if mkt * r1 > 0 and abs(mkt) >= .01 else " · 个股独立"
                add_fwd(("C 大涨大跌: 跟大盘还是独立", b + tag, "全部可交易"), rows, i, cost)
        # 2. next-day split: overnight gap vs intraday after a big up/down day
        if b and i + 1 < n and rows[i + 1][5] > 0:
            on = rows[i + 1][1] / rows[i][4] - 1
            intra = rows[i + 1][4] / rows[i + 1][1] - 1
            if abs(on) < 1 and abs(intra) < 1:
                R[("D 第二天拆开看", b + " · 隔夜(收盘→开盘)", "全部可交易", 1)].add(d, on, on)
                R[("D 第二天拆开看", b + " · 盘中(开盘→收盘)", "全部可交易", 1)].add(d, intra, intra - cost)
        # 3. US overnight on real stock opens (large stocks only)
        if size.startswith("大票") and i + 1 < n and rows[i + 1][5] > 0:
            nd = rows[i + 1][0]
            ud = prev_us(us_dates, nd)
            if ud and ud >= d:
                ub = us_bucket(us_ret[ud])
                gap = rows[i + 1][1] / rows[i][4] - 1
                intra = rows[i + 1][4] / rows[i + 1][1] - 1
                if abs(gap) < .5 and abs(intra) < .5:
                    R[("E 美股隔夜→澳洲大票(真实开盘价)", ub + " · 开盘跳空", "大票", 0)].add(nd, gap, gap)
                    R[("E 美股隔夜→澳洲大票(真实开盘价)", ub + " · 开盘后到收盘", "大票", 0)].add(nd, intra, intra - cost)
        # 4. weekday overnight (Friday close -> Monday open etc.)
        if i + 1 < n and rows[i + 1][5] > 0:
            nd = rows[i + 1][0]
            wd = ["周一", "周二", "周三", "周四", "周五"][min(4, date.fromisoformat(nd).weekday())]
            on = rows[i + 1][1] / rows[i][4] - 1
            intra = rows[i + 1][4] / rows[i + 1][1] - 1
            if abs(on) < .5 and abs(intra) < .5:
                R[("F 星期几: 隔夜 vs 盘中", wd + " · 隔夜", size, 0)].add(nd, on, on)
                R[("F 星期几: 隔夜 vs 盘中", wd + " · 盘中", size, 0)].add(nd, intra, intra)
        # 5. after 3+ down days for the stock itself
        if i >= 4 and all(rows[k][4] < rows[k - 1][4] for k in (i, i - 1, i - 2)):
            dd = rows[i][4] / rows[i - 3][4] - 1
            tag = "连跌3天 跌幅≥10%" if dd <= -.1 else "连跌3天 跌幅<10%"
            add_fwd(("G 个股连跌之后", tag, size), rows, i, cost)


def index_streaks(xjo):
    """Buy the ASX 200 at the close after N down days, hold H days; ETF cost 0.1%."""
    n = len(xjo)
    for i in range(6, n - 10):
        d = xjo[i][0]
        if d < "2015-01-01":
            continue
        k = 0
        while k < 6 and xjo[i - k][4] < xjo[i - k - 1][4]:
            k += 1
        if k >= 2:
            for h in (1, 2, 5, 10):
                g = xjo[i + h][4] / xjo[i][4] - 1
                STREAK[(f"连跌 {min(k, 5)}{'+' if k >= 5 else ''} 天", h)].add(d, g, g - .001)
        for h in (5,):
            g = xjo[i + h][4] / xjo[i][4] - 1
            STREAK[("任意一天(对照)", h)].add(d, g, g - .001)


def simulate_streak(xjo, need=3, hold=5):
    """Equity curve: in the index only for `hold` days after `need` down days."""
    eq, bh, pos, curve, inmkt = 1.0, 1.0, 0, [], 0
    start = next(i for i, r in enumerate(xjo) if r[0] >= "2015-01-01")
    for i in range(start, len(xjo)):
        r = xjo[i][4] / xjo[i - 1][4] - 1
        bh *= 1 + r
        if pos > 0:
            inmkt += 1
            eq *= 1 + r
            pos -= 1
            if pos == 0:
                eq *= 1 - .001
        elif all(xjo[k][4] < xjo[k - 1][4] for k in range(i - need + 1, i + 1)):
            pos = hold
        if i % 5 == 0:
            curve.append({"d": xjo[i][0], "s": round(eq, 4), "b": round(bh, 4)})
    years = (date.fromisoformat(xjo[-1][0]) - date.fromisoformat(xjo[start][0])).days / 365.25

    def mdd(key):
        peak, m = 0, 0
        for p in curve:
            peak = max(peak, p[key])
            m = min(m, p[key] / peak - 1)
        return m * 100
    return {"curve": curve, "cagr_s": (eq ** (1 / years) - 1) * 100, "cagr_b": (bh ** (1 / years) - 1) * 100,
            "mdd_s": mdd("s"), "mdd_b": mdd("b"), "years": years, "need": need, "hold": hold,
            "in_market": 100 * inmkt / max(1, len(xjo) - start)}


def main():
    t0 = time.time()
    uni = study.universe()
    syms = [f"{c}.AU" for c, _ in uni] + ["AXJO.INDX", "GSPC.INDX", "AXSO.INDX"]
    study.download(syms)
    xjo, spx = study.load("AXJO.INDX"), study.load("GSPC.INDX")
    xjo_ret = {xjo[k][0]: xjo[k][4] / xjo[k - 1][4] - 1 for k in range(1, len(xjo))}
    us_ret, us_dates = us_returns(spx)
    for k, (code, _) in enumerate(uni):
        rows = study.load(f"{code}.AU")
        if rows:
            scan(rows, xjo_ret, us_ret, us_dates)
        if (k + 1) % 300 == 0:
            print(f"\r  round 2: {k + 1}/{len(uni)}", end="", flush=True)
    print()
    index_streaks(xjo)
    sims = [simulate_streak(xjo, n, h) for n, h in ((2, 5), (3, 5), (3, 10), (4, 5))]
    rows = []
    for (st, b, g, h), a in R.items():
        s = a.summary()
        if s:
            rows.append({"study": st, "bucket": b, "group": g, "h": h, **s})
    streak = []
    for (b, h), a in STREAK.items():
        s = a.summary()
        if s:
            streak.append({"bucket": b, "h": h, **s})
    sp = sorted(SPREADS)
    out = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M"), "min_price": MIN_PRICE,
                    "min_value": MIN_VALUE, "minutes": round((time.time() - t0) / 60, 1),
                    "cost_median": sp[len(sp) // 2] * 100 if sp else None,
                    "cost_p25": sp[len(sp) // 4] * 100 if sp else None,
                    "cost_p75": sp[3 * len(sp) // 4] * 100 if sp else None},
           "rows": sorted(rows, key=lambda r: (r["study"], r["bucket"], r["group"], r["h"])),
           "streak": sorted(streak, key=lambda r: (r["bucket"], r["h"])), "sims": sims}
    p = Path(os.environ.get("STUDY2_OUT", study.DATA / "study2_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"round 2 done in {out['meta']['minutes']} min -> {p}")


if __name__ == "__main__":
    main()
