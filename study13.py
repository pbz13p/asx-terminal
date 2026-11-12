"""Round 13: classic short-term swing rules on the index (ETF-tradable: IOZ/STW for ASX 200, ISO for small caps).

Rules are textbook settings, not tuned. Signal at the close, trade at the next close (index has no
real open price in EODHD), 0.1% cost per round trip. Cash earns nothing.
  RSI2<10            buy when 2-day RSI < 10, sell when close > 5-day average
  RSI2<10 & >MA200   same, only when the index is above its 200-day average
  布林下轨           buy when close < 20-day average - 2 sd, sell when close > 20-day average
  连跌3天            buy after 3 down closes, hold 5 days
  月末月初           hold from the second-last trading day of the month to the 3rd trading day of next
"""
import json
import math
import os
from pathlib import Path

import study

COST = 0.001


def rsi2(c, i):
    g = l = 0.0
    for k in (i - 1, i):
        d = c[k] - c[k - 1]
        g += max(d, 0)
        l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def run(days, c, rule):
    pos, held, trades, cur = 0, 0, [], None
    daily = []
    month_idx = {}
    for i, d in enumerate(days):
        month_idx.setdefault(d[:7], []).append(i)
    tom = set()
    keys = sorted(month_idx)
    for a, b in zip(keys, keys[1:]):
        tom.update(month_idx[a][-2:-1])  # entry day (second-last)
    tom_exit = {month_idx[b][2] for b in keys[1:] if len(month_idx[b]) > 2}
    for i in range(201, len(days)):
        r = c[i] / c[i - 1] - 1
        daily.append(r * pos)
        if pos:
            cur[1] = c[i]
        ma5 = sum(c[i - 4:i + 1]) / 5
        ma20 = sum(c[i - 19:i + 1]) / 20
        sd20 = math.sqrt(sum((x - ma20) ** 2 for x in c[i - 19:i + 1]) / 20)
        ma200 = sum(c[i - 199:i + 1]) / 200
        enter = exit_ = False
        if rule == "RSI2<10":
            enter, exit_ = rsi2(c, i) < 10, c[i] > ma5
        elif rule == "RSI2<10 且在200日线上":
            enter, exit_ = rsi2(c, i) < 10 and c[i] > ma200, c[i] > ma5
        elif rule == "布林下轨":
            enter, exit_ = c[i] < ma20 - 2 * sd20, c[i] > ma20
        elif rule == "连跌3天":
            enter = c[i] < c[i - 1] < c[i - 2] < c[i - 3]
            exit_ = held >= 5
        elif rule == "月末月初":
            enter, exit_ = i in tom, i in tom_exit
        if pos:
            held += 1
            if exit_:
                pos = 0
                trades.append(cur[1] / cur[0] - 1 - COST)
                daily[-1] -= COST
        elif enter:
            pos, held, cur = 1, 0, [c[i], c[i]]
    return daily, trades


def stats(daily, days):
    eq = peak = 1.0
    dd = 0.0
    for r in daily:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    yrs = len(daily) / 252
    m = sum(daily) / len(daily)
    sd = math.sqrt(sum((r - m) ** 2 for r in daily) / len(daily))
    return {"cagr": 100 * (eq ** (1 / yrs) - 1), "maxdd": 100 * dd, "sharpe": m / sd * math.sqrt(252) if sd else 0,
            "exposure": 100 * sum(1 for r in daily if r != 0) / len(daily)}


def main():
    out = {}
    for name, sym in (("ASX 200", "AXJO.INDX"), ("小盘 Small Ords", "AXSO.INDX")):
        study.download([sym])
        rows = [r for r in study.load(sym) if r[0] >= "2014-01-01"]
        days, c = [r[0] for r in rows], [r[4] for r in rows]
        res = {}
        bh = [c[i] / c[i - 1] - 1 for i in range(202, len(c))]
        res["一直持有"] = {"all": stats(bh, days)}
        for rule in ("RSI2<10", "RSI2<10 且在200日线上", "布林下轨", "连跌3天", "月末月初"):
            daily, trades = run(days, c, rule)
            dd = days[202:202 + len(daily)]
            split = next((k for k, d in enumerate(dd) if d >= study.SPLIT), len(dd))
            tr = trades
            res[rule] = {"all": stats(daily, dd), "in": stats(daily[:split], dd[:split]), "out": stats(daily[split:], dd[split:]),
                         "trades": len(tr), "avg_trade": 100 * sum(tr) / len(tr) if tr else None,
                         "win": 100 * sum(1 for t in tr if t > 0) / len(tr) if tr else None}
        out[name] = res
    p = Path(os.environ.get("STUDY13_OUT", study.DATA / "study13_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
