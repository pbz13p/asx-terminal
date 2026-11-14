"""Round 63: asymmetric returns -- stocks that fall less when the market falls and rise more when it rises.

Each month end (2014 on), universe = the N most traded ASX stocks (60-day median value traded, price >= $0.50,
>= 1 year of history, delisted stocks included). Everything uses data up to that close only.

Asymmetry measures (past 1 year, daily, vs the ASX 200):
  up/down capture, capture ratio, skewness, downside vs upside volatility, max drawdown, distance from 52w high/low
Forward outcome (next 60 sessions): best gain (max close), worst fall (min close), gain/pain = best / |worst|,
  "asymmetry" = log(1+best) + log(1+worst)  (> 0 means the upside seen was bigger than the downside)

Models, each picks the top K stocks every month, equal weight, held one month, 0.2% cost per unit of turnover:
  1 naive: best past-year capture ratio
  2 score: rank sum of capture ratio, skew, upside/downside volatility, past max drawdown
  3 LightGBM: predicts each stock's forward asymmetry rank (walk-forward, refit every 6 months, embargoed)
A model is kept only if it beats the previous one. Everything is compared with the ASX 200 accumulation index,
with beta stripped out (alpha from a monthly regression) to check it is not just high beta / lottery stocks.
"""
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import study

N = int(os.environ.get("S63_N", "200"))
K = int(os.environ.get("S63_K", "20"))
FWD = 60
MINVAL = float(os.environ.get("S63_MINVAL", "1"))       # 60-day median $ traded per day
PRICE_MIN = float(os.environ.get("S63_PRICE", "0.5"))
COST = 0.002
FEATS = ["up_cap", "down_cap", "cap_ratio", "skew", "updown_vol", "down_vol", "vol60", "beta", "maxdd_1y",
         "from_high", "from_low", "mom_12_1", "ret_1m", "log_value", "value_trend"]


def panel():
    acc = pd.Series({r[0]: r[4] for r in study.load("AXJOA.INDX")}).sort_index()
    xjo = pd.Series({r[0]: r[4] for r in study.load("AXJO.INDX")}).sort_index()
    cal = [d for d in acc.index if d >= "2013-01-01"]
    closes, values = {}, {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = [r for r in study.load(f.name[:-5]) if r[0] >= "2013-01-01"]
        if len(rows) < 260:
            continue
        code = f.name[:-8]
        closes[code] = pd.Series([r[4] for r in rows], index=[r[0] for r in rows])
        values[code] = pd.Series([r[6] * r[5] for r in rows], index=[r[0] for r in rows])
    C = pd.DataFrame(closes).reindex(cal)
    V = pd.DataFrame(values).reindex(cal)
    return C, V, acc.reindex(cal).ffill(), xjo.reindex(cal).ffill()


def features_at(t0, C, V, R, mr, codes):
    w = R.iloc[t0 - 251:t0 + 1][codes]
    m = mr.iloc[t0 - 251:t0 + 1]
    up, dn = (m > 0).values, (m < 0).values
    X = pd.DataFrame(index=codes)
    X["up_cap"] = w[up].mean() / m[up].mean()
    X["down_cap"] = w[dn].mean() / m[dn].mean()
    X["cap_ratio"] = X.up_cap / X.down_cap.where(X.down_cap > 0.05, 0.05)
    X["skew"] = w.skew()
    neg, pos = w.where(w < 0), w.where(w > 0)
    X["down_vol"] = np.sqrt((neg ** 2).mean()) * math.sqrt(252)
    X["updown_vol"] = np.sqrt((pos ** 2).mean()) / np.sqrt((neg ** 2).mean())
    X["vol60"] = w.iloc[-60:].std() * math.sqrt(252)
    cov = w.apply(lambda s: s.cov(m))
    X["beta"] = cov / m.var()
    px = C.iloc[t0 - 251:t0 + 1][codes].ffill()
    X["maxdd_1y"] = (px / px.cummax() - 1).min()
    X["from_high"] = px.iloc[-1] / px.max() - 1
    X["from_low"] = px.iloc[-1] / px.min() - 1
    X["mom_12_1"] = px.iloc[-22] / px.iloc[0] - 1
    X["ret_1m"] = px.iloc[-1] / px.iloc[-22] - 1
    v = V.iloc[t0 - 59:t0 + 1][codes].median()
    X["log_value"] = np.log(v)
    X["value_trend"] = np.log(V.iloc[t0 - 19:t0 + 1][codes].median() / v)
    return X.replace([np.inf, -np.inf], np.nan)


def stats(r, b):
    r, b = pd.Series(r), pd.Series(b).reindex(pd.Series(r).index)
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    beta, a = np.polyfit(b, r, 1)
    resid = r - (a + beta * b)
    upm, dnm = b > 0, b < 0
    return {"cagr": round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 1), "vol": round(100 * r.std() * math.sqrt(12), 1),
            "sharpe": round(float(r.mean() / r.std() * math.sqrt(12)), 2), "maxdd": round(100 * float((eq / eq.cummax() - 1).min()), 1),
            "up_capture": round(100 * r[upm].mean() / b[upm].mean()), "down_capture": round(100 * r[dnm].mean() / b[dnm].mean()),
            "beta": round(float(beta), 2), "alpha_pa": round(100 * a * 12, 1),
            "alpha_t": round(float(a / (resid.std() / math.sqrt(len(r)))), 2), "months": int(len(r))}


def main():
    t_start = time.time()
    C, V, acc, xjo = panel()
    R = C.pct_change(fill_method=None)
    R = R.where(R.abs() < 1.5)
    mr = xjo.pct_change()
    me = pd.Series(C.index, index=C.index).groupby(C.index.str[:7]).last().values
    me = [d for d in me if d >= "2014-01-01"]
    pos = {d: C.index.get_loc(d) for d in me}
    rows = []  # one per (month, stock)
    for i, d0 in enumerate(me):
        t0 = pos[d0]
        if t0 < 260:
            continue
        v = V.iloc[t0 - 59:t0 + 1].median()
        ok = (v >= MINVAL) & (C.iloc[t0] >= PRICE_MIN) & R.iloc[t0 - 251:t0 + 1].notna().sum().ge(200)
        codes = list(v[ok].sort_values(ascending=False).index[:N])
        if len(codes) < min(N, 150) * .8:
            continue
        X = features_at(t0, C, V, R, mr, codes)
        X["size_rank"] = range(1, len(codes) + 1)
        # forward outcomes (delisted: last traded price carried)
        seg = C.iloc[t0:t0 + FWD + 1][codes].ffill()
        c0 = seg.iloc[0]
        X["fwd_best"] = seg.max() / c0 - 1
        X["fwd_worst"] = seg.min() / c0 - 1
        X["fwd_ret60"] = seg.iloc[-1] / c0 - 1
        X["fwd_complete"] = t0 + FWD < len(C)
        X["asym"] = np.log1p(X.fwd_best) + np.log1p(X.fwd_worst.clip(lower=-0.99))
        if i + 1 < len(me):
            nxt = C.iloc[t0:pos[me[i + 1]] + 1][codes].ffill()
            X["next_ret"] = (nxt.iloc[-1] / nxt.iloc[0] - 1).clip(lower=-1)
            X["bench_next"] = acc.iloc[pos[me[i + 1]]] / acc.iloc[t0] - 1
        else:
            X["next_ret"], X["bench_next"] = np.nan, np.nan
        X["month"], X["t0"], X["code"] = d0[:7], t0, X.index
        rows.append(X.reset_index(drop=True))
    P = pd.concat(rows, ignore_index=True)
    P["asym_rank"] = P.groupby("month")["asym"].rank(pct=True)
    print("[panel]", len(P), "rows", P.month.nunique(), "months", f"{time.time() - t_start:.0f}s")

    # model scores
    P["s_naive"] = P.cap_ratio
    rk = lambda c: P.groupby("month")[c].rank(pct=True)
    P["s_score"] = rk("cap_ratio") + rk("skew") + rk("updown_vol") + rk("maxdd_1y")
    import lightgbm as lgb
    params = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7)
    P["s_lgb"] = np.nan
    months = sorted(P.month.unique())
    t_of = P.groupby("month").t0.first()
    importance = pd.Series(0.0, index=FEATS)
    for k in range(0, len(months), 6):
        test = months[k:k + 6]
        cut = t_of[test[0]] - FWD - 5  # only rows whose 60-day outcome was known before this month
        tr = P[(P.t0 <= cut) & P.fwd_complete & P.asym_rank.notna()]
        if len(tr) < int(os.environ.get("S63_MINTRAIN", "6000")):
            continue
        m = lgb.train(params, lgb.Dataset(tr[FEATS], tr.asym_rank), num_boost_round=300)
        importance += pd.Series(m.feature_importance("gain"), index=FEATS)
        te = P.month.isin(test)
        P.loc[te, "s_lgb"] = m.predict(P.loc[te, FEATS])
    first_lgb = P.loc[P.s_lgb.notna(), "month"].min()
    print("[lgb] first test month", first_lgb, f"{time.time() - t_start:.0f}s")

    def portfolio(score, mask=None, k=K):
        rets, bench, picks_asym, univ_asym, beta, small, prev, out_m = [], [], [], [], [], [], set(), []
        for mth, G in P.groupby("month"):
            if mask is not None:
                G = G[mask(G)]
            G = G[G[score].notna() & G.next_ret.notna()]
            if len(G) < k * 2:
                continue
            top = G.nlargest(k, score)
            names = set(top.code)
            turn = 1 - len(names & prev) / k
            prev = names
            rets.append(float(top.next_ret.mean()) - COST * 2 * turn)
            bench.append(float(G.bench_next.iloc[0]))
            done = G[G.fwd_complete]
            if len(done):
                t2 = done.nlargest(k, score)
                picks_asym.append((float(t2.fwd_best.mean()), float(t2.fwd_worst.mean()), float(t2.asym_rank.mean())))
                univ_asym.append((float(done.fwd_best.mean()), float(done.fwd_worst.mean())))
            beta.append(float(top.beta.mean()))
            small.append(float((top.size_rank > 50).mean()))
            out_m.append(mth)
        if len(rets) < 12:
            return {"all": None, "picks_asym_rank": None, "bench": {"cagr": None}}, None
        r, b = pd.Series(rets, index=out_m), pd.Series(bench, index=out_m)
        pa, ua = np.array(picks_asym), np.array(univ_asym)
        res = {"all": stats(r, b), "bench": stats(b, b),
               "picks_fwd60_best": round(100 * pa[:, 0].mean(), 1), "picks_fwd60_worst": round(100 * pa[:, 1].mean(), 1),
               "picks_asym_rank": round(100 * pa[:, 2].mean(), 1),
               "universe_fwd60_best": round(100 * ua[:, 0].mean(), 1), "universe_fwd60_worst": round(100 * ua[:, 1].mean(), 1),
               "avg_beta_of_picks": round(float(np.mean(beta)), 2), "share_outside_top50": round(100 * float(np.mean(small)))}
        per = {}
        for lab, lo, hi in (("2014–21", "2014", "2021-12"), ("2022–23", "2022", "2023-12"), ("2024 后", "2024", "9999")):
            mm = (r.index >= lo) & (r.index <= hi)
            if mm.sum() >= 12:
                per[lab] = {"model": stats(r[mm], b[mm]), "bench_cagr": stats(b[mm], b[mm])["cagr"]}
        res["by_period"] = per
        return res, r

    names = [("s_naive", "1 最笨基准：过去一年捕获比最高"), ("s_score", "2 简单打分"), ("s_lgb", "3 LightGBM")]
    common = lambda G: G.month >= first_lgb
    out = {"method": __doc__.strip(), "first_lgb_month": first_lgb, "k": K, "n": N, "models": {}, "groups": {}}
    for s, nm in names:
        full, _ = portfolio(s) if s != "s_lgb" else (None, None)
        same, _ = portfolio(s, common)
        out["models"][nm] = {"full_period": full, "common_period": same}
        print(nm, json.dumps(same["all"], ensure_ascii=False), "picks asym rank", same["picks_asym_rank"])
        for gname, gm in (("大盘（成交额前 50）", lambda G: (G.size_rank <= 50) & (G.month >= first_lgb)),
                          ("中盘（51–200）", lambda G: (G.size_rank > 50) & (G.size_rank <= 200) & (G.month >= first_lgb)),
                          ("小盘（200 名以后）", lambda G: (G.size_rank > 200) & (G.month >= first_lgb))):
            gr, _ = portfolio(s, gm, k=max(2, K // 2))
            out["groups"].setdefault(gname, {})[nm] = {"all": gr["all"], "picks_asym_rank": gr["picks_asym_rank"],
                                                       "bench_cagr": gr["bench"]["cagr"]}
    # ladder: keep a model only if it beats the previous kept one on Sharpe and alpha over the common period
    kept, ladder = names[0], []
    for s, nm in names[1:]:
        a, b = out["models"][nm]["common_period"]["all"], out["models"][kept[1]]["common_period"]["all"]
        win = a["sharpe"] > b["sharpe"] and a["alpha_pa"] > b["alpha_pa"]
        ladder.append({"model": nm, "vs": kept[1], "beats": bool(win), "sharpe": a["sharpe"], "vs_sharpe": b["sharpe"],
                       "alpha": a["alpha_pa"], "vs_alpha": b["alpha_pa"]})
        if win:
            kept = (s, nm)
    out["ladder"], out["chosen"] = ladder, kept[1]
    bench_c = out["models"][names[0][1]]["common_period"]["bench"]
    best = out["models"][kept[1]]["common_period"]["all"]
    out["beats_asx200"] = bool(best["sharpe"] > bench_c["sharpe"] and best["alpha_t"] > 2)
    out["lgb_importance"] = {k: round(float(v / importance.sum() * 100), 1) for k, v in importance.sort_values(ascending=False).items()} if importance.sum() else {}
    # today's candidates with the chosen model (LightGBM refit on everything known)
    last = P[P.month == P.month.max()].copy()
    if kept[0] == "s_lgb":
        tr = P[P.fwd_complete & (P.t0 <= last.t0.iloc[0] - FWD - 5)]
        m = lgb.train(params, lgb.Dataset(tr[FEATS], tr.asym_rank), num_boost_round=300)
        last["pick"] = m.predict(last[FEATS])
    else:
        last["pick"] = last[kept[0]]
    top = last.nlargest(K, "pick")
    try:
        import volml
        info = {c: volml.company_info(c) for c in top.code}
    except Exception:
        info = {}
    out["candidates"] = [{"code": r.code, "name": info.get(r.code, {}).get("displayName"), "sector": info.get(r.code, {}).get("sector"),
                          "mcap": info.get(r.code, {}).get("marketCap"), "up_cap": round(float(r.up_cap) * 100),
                          "down_cap": round(float(r.down_cap) * 100), "cap_ratio": round(float(r.cap_ratio), 2),
                          "skew": round(float(r.skew), 2), "beta": round(float(r.beta), 2), "maxdd_1y": round(100 * float(r.maxdd_1y)),
                          "from_high": round(100 * float(r.from_high)), "size_rank": int(r.size_rank), "score": round(float(r.pick), 3)}
                         for r in top.itertuples()]
    out["as_of"] = C.index[-1]
    out["minutes"] = round((time.time() - t_start) / 60, 1)
    p = Path(os.environ.get("STUDY63_OUT", study.DATA / "study63_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("chosen", out["chosen"], "beats ASX200:", out["beats_asx200"], [c["code"] for c in out["candidates"]])


if __name__ == "__main__":
    main()
