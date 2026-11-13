"""Round 62: low-volatility anomaly on the ASX.

Each month end, take the N most traded stocks (60-day median value traded, price >= $0.50, all stocks including
ones later delisted), rank them by trailing volatility (60-day, and a 1-year variant), split into five equal groups,
hold each group equal-weighted for the next month. Adjusted closes (dividends and splits included).
Costs: 0.2% per unit of one-way turnover (commission + spread). Delisted stocks: last traded price is used.
Compared with the ASX 200 accumulation index. Also: the 30 lowest-volatility stocks, and a beta-matched check
(alpha from a monthly regression on the index), and results per period.
"""
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

import study

COST = 0.002


def load_panel(start="2013-06-01"):
    closes, values = {}, {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 150:
            continue
        rows = [r for r in rows if r[0] >= start]
        if len(rows) < 150:
            continue
        code = f.name[:-8]
        idx = [r[0] for r in rows]
        closes[code] = pd.Series([r[4] for r in rows], index=idx)
        values[code] = pd.Series([r[6] * r[5] for r in rows], index=idx)
    C = pd.DataFrame(closes).sort_index()
    V = pd.DataFrame(values).reindex(C.index)
    return C, V


def stats(r, bench=None):
    r = pd.Series(r).dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    out = {"cagr": round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 1), "vol": round(100 * r.std() * math.sqrt(12), 1),
           "sharpe": round(r.mean() / r.std() * math.sqrt(12), 2) if r.std() else None,
           "maxdd": round(100 * float((eq / eq.cummax() - 1).min()), 1), "months": int(len(r))}
    if bench is not None:
        b = pd.Series(bench).reindex(r.index)
        ok = b.notna()
        beta, a = np.polyfit(b[ok], r[ok], 1)
        resid = r[ok] - (a + beta * b[ok])
        out["beta"] = round(float(beta), 2)
        out["alpha_pa"] = round(100 * a * 12, 1)
        out["alpha_t"] = round(float(a / (resid.std() / math.sqrt(ok.sum()))), 2)
        out["excess_pa"] = round(100 * (r[ok] - b[ok]).mean() * 12, 1)
    return out


def main():
    C, V = load_panel()
    acc = pd.Series({r[0]: r[4] for r in study.load("AXJOA.INDX")})
    C = C.loc[C.index >= acc.index.min()]
    V = V.reindex(C.index)
    R = C.pct_change(fill_method=None)
    R = R.where(R.abs() < 1.5)
    month_ends = C.index.to_series().groupby(C.index.str[:7]).last().values
    month_ends = [d for d in month_ends if d >= "2014-06-01"]
    out = {"method": __doc__.strip(), "variants": {}}
    for N in (100, 200):
        for look in (60, 252):
            name = f"前 {N} 大 · {'60 日' if look == 60 else '1 年'}波动"
            q_ret = {k: [] for k in range(5)}
            low30, months, bench, prev = [], [], [], {k: set() for k in range(5)}
            prev30 = set()
            for i in range(len(month_ends) - 1):
                d0, d1 = month_ends[i], month_ends[i + 1]
                t0 = C.index.get_loc(d0)
                if t0 < look:
                    continue
                win_v = V.iloc[t0 - 59:t0 + 1].median()
                price = C.iloc[t0]
                ok = (win_v > 0) & (price >= 0.5) & R.iloc[t0 - look + 1:t0 + 1].notna().sum().ge(int(look * .8))
                elig = win_v[ok].sort_values(ascending=False).index[:N]
                if len(elig) < N * .8:
                    continue
                vol = R.iloc[t0 - look + 1:t0 + 1][elig].std().dropna().sort_values()
                # next-month return per stock; delisted -> last available price in the month
                seg = C.iloc[t0:C.index.get_loc(d1) + 1][vol.index]
                last = seg.ffill().iloc[-1]
                ret = (last / seg.iloc[0] - 1).clip(lower=-1)
                groups = np.array_split(vol.index.values, 5)
                for k, g in enumerate(groups):
                    g = set(g)
                    turn = 1 - len(g & prev[k]) / max(1, len(g))
                    q_ret[k].append(float(ret[list(g)].mean()) - COST * turn * 2)
                    prev[k] = g
                g30 = set(vol.index[:30])
                turn = 1 - len(g30 & prev30) / 30
                low30.append(float(ret[list(g30)].mean()) - COST * turn * 2)
                prev30 = g30
                months.append(d1[:7])
                bench.append(float(acc[acc.index <= d1].iloc[-1] / acc[acc.index <= d0].iloc[-1] - 1))
            b = pd.Series(bench, index=months)
            res = {"bench": stats(b)}
            for k in range(5):
                res[f"Q{k + 1}"] = stats(pd.Series(q_ret[k], index=months), b)
            res["最低波动 30 只"] = stats(pd.Series(low30, index=months), b)
            per = {}
            for lab, lo, hi in (("2014–21", "2014", "2021-12"), ("2022–23", "2022", "2023-12"), ("2024 后", "2024", "9999")):
                m = [(x >= lo and x <= hi) for x in months]
                bb = b[m]
                per[lab] = {"bench": stats(bb)["cagr"], "Q1": stats(pd.Series(q_ret[0], index=months)[m], bb),
                            "Q5": stats(pd.Series(q_ret[4], index=months)[m], bb),
                            "最低波动 30 只": stats(pd.Series(low30, index=months)[m], bb)}
            res["by_period"] = per
            out["variants"][name] = res
            print(name, json.dumps({k: res[k] for k in ("bench", "Q1", "Q3", "Q5", "最低波动 30 只")}, ensure_ascii=False))
    # today's lowest-volatility names (top 200, 1-year), for the app
    t0 = len(C) - 1
    win_v = V.iloc[t0 - 59:t0 + 1].median()
    ok = (win_v > 0) & (C.iloc[t0] >= 0.5) & R.iloc[t0 - 251:t0 + 1].notna().sum().ge(200)
    elig = win_v[ok].sort_values(ascending=False).index[:200]
    vol = R.iloc[t0 - 251:t0 + 1][elig].std().dropna().sort_values()
    out["today_low30"] = [{"code": c, "vol_1y": round(float(v) * math.sqrt(252) * 100, 1)} for c, v in vol.iloc[:30].items()]
    out["as_of"] = C.index[-1]
    p = Path(os.environ.get("STUDY62_OUT", study.DATA / "study62_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
