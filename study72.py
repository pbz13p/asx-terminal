"""Round 72: the same 34 indicators / 32 textbook signals / pairs / LightGBM as round 71, on China A-shares.

Data: EODHD daily prices for every Shanghai + Shenzhen stock (delisted ones included), main board only
(60xxxx, 000/001/002/003xxxx; ChiNext 300/301 and STAR 688/689 are closed to individual Stock Connect investors).
Sampled every 5 trading days from 2010. Universe at each sample: has traded in the last 10 days, 60-day median value
above the bottom 20% of that day. Groups: "big" = top 1000 by 60-day value (rough stand-in for Stock Connect
eligibility), "small" = the rest.
Tradability: a signal on a day that closed limit-up (>= +9.7%) can't be bought at the close -> dropped. At the exit,
a limit-down close (<= -9.7%) can't be sold -> exit moves to the next day that is not limit-down (up to 10 days).
Cost per round trip (IBKR China Connect): commission 0.08% x2, stamp duty 0.05% on the sale, exchange fees ~0.02%,
plus one price step (RMB 0.01) of spread. Outcome = 20-day return minus the same group's average that day.
Extra China-specific signals: 5-day losers (short-term reversal), yesterday limit-up, turnover surge.
"""
import gzip
import itertools
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import study71 as s71

CN = Path("data/cn")
STEP, H = 5, 20
MAIN = ("60", "000", "001", "002", "003")


def load(f):
    rows = json.loads(gzip.decompress(f.read_bytes()))
    if len(rows) < 260:
        return None
    df = pd.DataFrame(rows, columns=["d", "o", "h", "l", "c", "adj", "v"]).dropna(subset=["c", "adj"])
    df = df[(df.c > 0) & ~df.d.duplicated()].set_index("d")
    k = df.adj / df.c
    return pd.DataFrame({"o": df.o * k, "h": df.h * k, "l": df.l * k, "c": df.adj, "raw": df.c, "v": df.v.fillna(0)})


def build():
    files = [f for f in sorted(CN.glob("*.json.gz")) if f.name.startswith(MAIN)]
    cnt = {}
    for f in files[::7]:
        rows = json.loads(gzip.decompress(f.read_bytes()))
        for r in rows:
            cnt[r[0]] = cnt.get(r[0], 0) + 1
    cal = pd.Index(sorted(d for d, n in cnt.items() if n >= int(os.environ.get('S72_MINCAL', '20'))))
    samples = cal[cal >= "2010-01-01"][::STEP]
    parts = []
    t0 = time.time()
    for i, f in enumerate(files):
        df = load(f)
        if df is None:
            continue
        df = df.reindex(cal)
        traded = df.c.notna()
        if traded.sum() < 260:
            continue
        last = traded[traded].index[-1]
        c = df.c.ffill(limit=10)
        h, l = df.h.fillna(c).ffill(limit=10), df.l.fillna(c).ffill(limit=10)
        v = df.v.fillna(0)
        raw = df.raw.ffill(limit=10)
        F = s71.indicators(c, h, l, v)
        F["close"], F["raw"] = c, raw
        F["value60"] = (raw * v).rolling(60, min_periods=40).median()
        F["ret5"] = c / c.shift(5) - 1
        r1 = df.c / df.c.ffill().shift() - 1  # NaN on suspended days
        F["limit_up_today"] = r1 >= 0.097
        F["limit_up_yday"] = (r1.shift() >= 0.097).fillna(False)
        F["traded_10d"] = traded.rolling(10).sum() > 0
        # exit price 20 sessions later, pushed past limit-down days; delisted -> last traded price
        cl = df.c.ffill()
        ld = (r1 <= -0.097).fillna(False).values
        n = len(cal)
        ex = np.full(n, np.nan)
        clv = cl.values
        for t in np.where(F.index.isin(samples))[0]:
            e = t + H
            if e >= n:
                continue
            k = 0
            while e < n - 1 and ld[e] and k < 10:
                e += 1
                k += 1
            ex[t] = clv[e]
        F["fwd20"] = ex / c.values - 1
        S = F.loc[F.index.isin(samples) & traded.fillna(False).values & (F.index <= last)]
        S = S[S.traded_10d & S.fwd20.notna()]
        if len(S):
            parts.append(S.assign(code=f.name[:-8], date=S.index).reset_index(drop=True))
        if i % 500 == 0:
            print("[build]", i, len(files), f"{time.time() - t0:.0f}s")
    P = pd.concat(parts, ignore_index=True).replace([np.inf, -np.inf], np.nan)
    P = P[P.groupby("date").value60.rank(pct=True) > 0.2]
    P["rk"] = P.groupby("date").value60.rank(ascending=False)
    P["grp"] = np.where(P.rk <= int(os.environ.get('S72_BIG', '1000')), "big", "small")
    P["ex20"] = P.fwd20.clip(-1, 5) - P.groupby(["date", "grp"]).fwd20.transform(lambda s: s.clip(-1, 5).mean())
    P["cost"] = 0.0016 + 0.0005 + 0.0002 + 0.01 / P.raw
    P["mom_rank"] = P.groupby(["date", "grp"]).mom_12_1.rank(pct=True)
    P["ret5_rank"] = P.groupby(["date", "grp"]).ret5.rank(pct=True)
    P["turn_rank"] = P.groupby(["date", "grp"]).vol_ratio.rank(pct=True)
    return P


SIGNALS = dict(s71.SIGNALS)
SIGNALS.update({
    "近 5 日跌幅最大 10%（短期反转）": lambda X: X.ret5_rank <= 0.1,
    "近 5 日涨幅最大 10%": lambda X: X.ret5_rank > 0.9,
    "昨天涨停": lambda X: X.limit_up_yday,
    "成交突然放大（量比前 10%）": lambda X: X.turn_rank > 0.9,
})


def mask(P, name):
    fn = SIGNALS[name]
    return (P.mom_rank > 0.8) if fn is None else fn(P).fillna(False).astype(bool)


def evaluate(S):
    if len(S) < 30:
        return None
    t, nd = s71.tstat(S.ex20, S.date, H)
    net = S.ex20 - S.cost
    return {"n": int(len(S)), "dates": nd, "excess": round(100 * float(S.ex20.mean()), 2), "excess_net": round(100 * float(net.mean()), 2),
            "hit": round(100 * float((S.ex20 > 0).mean()), 1), "t": round(t, 2) if t is not None else None,
            "cost": round(100 * float(S.cost.median()), 2)}


def main():
    t0 = time.time()
    P = build()
    buyable = ~P.limit_up_today.fillna(False).astype(bool)
    print("[panel]", len(P), "rows", P.code.nunique(), "stocks", f"{time.time() - t0:.0f}s")
    out = {"method": __doc__.strip(), "rows": int(len(P)), "stocks": int(P.code.nunique()), "dates": int(P.date.nunique()),
           "first": P.date.min(), "last": P.date.max(), "median_cost_pct": round(100 * float(P.cost.median()), 2)}
    masks = {k: mask(P, k) & buyable for k in SIGNALS}
    out["A_signals"] = [{"signal": k, **{g: evaluate(P[m & (P.grp == g)]) for g in ("big", "small")}} for k, m in masks.items()]
    print("[A]", f"{time.time() - t0:.0f}s")
    B = []
    for col in s71.NUM + ["ret5"]:
        row = {"indicator": col}
        for g in ("big", "small"):
            G = P[P.grp == g][["date", col, "ex20"]].dropna()
            ic = pd.Series({dt: s[col].rank().corr(s.ex20.rank()) for dt, s in G.groupby("date") if len(s) > int(os.environ.get('S72_MINIC', '30'))}, dtype=float).dropna()
            eff = max(2, len(ic) * STEP / H)
            row[g] = {"ic": round(float(ic.mean()), 4) if len(ic) else None,
                      "t": round(float(ic.mean() / (ic.std() / math.sqrt(eff))), 2) if len(ic) > 20 else None}
        B.append(row)
    out["B_ic"] = sorted(B, key=lambda r: -abs(r["big"]["ic"] or 0))
    print("[B]", f"{time.time() - t0:.0f}s")
    ins = P.date < "2019-01-01"
    C = {}
    for g in ("big", "small"):
        gm = P.grp == g
        cand = []
        for a, b in itertools.combinations(list(masks), 2):
            m = masks[a] & masks[b] & gm
            if m.sum() < 300:
                continue
            ri, ro = evaluate(P[m & ins]), evaluate(P[m & ~ins])
            if ri and ri["t"] is not None:
                cand.append({"pair": f"{a} + {b}", "in_sample": ri, "out_of_sample": ro})
        cand.sort(key=lambda r: -(r["in_sample"]["excess_net"] if r["in_sample"]["t"] and r["in_sample"]["t"] > 2 else -99))
        top = cand[:20]
        C[g] = {"pairs_tested": len(cand), "top20_in_sample": top,
                "top20_still_positive_after_2019": sum(1 for r in top if r["out_of_sample"] and r["out_of_sample"]["excess_net"] > 0)}
    out["C_pairs"] = C
    print("[C]", f"{time.time() - t0:.0f}s")
    import lightgbm as lgb
    feats = s71.NUM + ["ret5", "log_price", "log_value"]
    P["log_price"], P["log_value"] = np.log(P.raw), np.log(P.value60 + 1)
    params = dict(objective="regression", learning_rate=0.03, num_leaves=31, min_data_in_leaf=1000, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7)
    P["pred"] = np.nan
    imp = pd.Series(0.0, index=feats)
    for y in sorted(P.date.str[:4].unique()):
        tr = P[P.date < f"{int(y) - 1}-12-01"]
        if tr.date.nunique() < 150:
            continue
        mdl = lgb.train(params, lgb.Dataset(tr[feats], tr.ex20.clip(-.5, 1)), num_boost_round=300)
        imp += pd.Series(mdl.feature_importance("gain"), index=feats)
        te = P.date.str[:4] == y
        P.loc[te, "pred"] = mdl.predict(P.loc[te, feats])
    T = P[P.pred.notna() & buyable]
    D = {"first_year": T.date.min()[:4] if len(T) else None,
         "importance": {k: round(float(v / imp.sum() * 100), 1) for k, v in imp.sort_values(ascending=False).items()} if imp.sum() else {}}
    for g in ("big", "small"):
        G = T[T.grp == g]
        if not len(G):
            continue
        G = G.assign(q=G.groupby("date").pred.rank(pct=True))
        top = G[G.q > .9]
        D[g] = {"top10": evaluate(top), "bottom10": evaluate(G[G.q <= .1]),
                "top10_net_by_year": {y: round(100 * float((top.ex20 - top.cost)[top.date.str[:4] == y].mean()), 2) for y in sorted(top.date.str[:4].unique())},
                "top10_abs_20d": round(100 * float(top.fwd20.clip(-1, 5).mean()), 2), "group_abs_20d": round(100 * float(G.fwd20.clip(-1, 5).mean()), 2)}
    out["D_lgb"] = D
    out["seconds"] = round(time.time() - t0)
    path = os.environ.get("STUDY72_OUT", "study72_results.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=float)
    print(json.dumps(D, ensure_ascii=False)[:2500])
    print("[done]", out["seconds"], "s")


if __name__ == "__main__":
    main()
