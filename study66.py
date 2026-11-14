"""Round 66: multibaggers -- what ASX stocks looked like BEFORE they doubled or went 10x, and whether that can be
found in advance.

Data: every ASX stock in the cache (delisted ones included, so failures count), 2014 on, adjusted prices.
Snapshots at each month end for stocks trading >= $10k/day (60-day median) and >= 0.5c. Features use data up to
that close only. Outcome over the next 12 months (252 sessions; delisted -> last traded price):
  peak multiple = highest close / entry, end multiple = close after 12 months / entry.
Questions:
  1 base rates: how often does a stock double / 5x / 10x within 12 months, by price and size
  2 what did the starting point look like (lift tables by feature bucket)
  3 case studies: the biggest runs since 2018 (incl. EOS, IVZ, PNN) and their starting features
  4 can it be picked in advance: LightGBM walk-forward for "doubles within 12 months", top 30 each month,
    outcome distribution after costs (3% round trip, small-cap spreads) vs all small caps vs the ASX 200
"""
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import study

H = 252
COST = 0.03
FEATS = ["log_price", "log_value", "value_surge", "ret_1m", "ret_3m", "ret_12m", "from_high_3y", "from_low_1y",
         "vol_60", "range_60", "above_ma50", "above_ma200", "zero_days_60", "up_days_20", "max_day_20", "beta_like"]
CASES = ["EOS", "IVZ", "PNN"]


def panel():
    acc = pd.Series({r[0]: r[4] for r in study.load("AXJOA.INDX")}).sort_index()
    cal = list(acc.index)
    C, Raw, V = {}, {}, {}
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 120:
            continue
        code = f.name[:-8]
        idx = [r[0] for r in rows]
        C[code] = pd.Series([r[4] for r in rows], index=idx)
        Raw[code] = pd.Series([r[6] for r in rows], index=idx)
        V[code] = pd.Series([r[6] * r[5] for r in rows], index=idx)
    C = pd.DataFrame(C).reindex(cal)
    return C, pd.DataFrame(Raw).reindex(cal), pd.DataFrame(V).reindex(cal).fillna(0), acc


def bucket_table(P, col, edges, labels):
    b = pd.cut(P[col], edges, labels=labels)
    g = P.groupby(b, observed=True)
    base2, base10 = P.hit2.mean(), P.hit10.mean()
    rows = []
    for lab, G in g:
        rows.append({"bucket": str(lab), "n": int(len(G)), "p_2x": round(100 * G.hit2.mean(), 1), "lift_2x": round(G.hit2.mean() / base2, 2),
                     "p_5x": round(100 * G.hit5.mean(), 2), "p_10x": round(100 * G.hit10.mean(), 2),
                     "lift_10x": round(G.hit10.mean() / base10, 2) if base10 else None,
                     "median_12m": round(100 * (G.end_mult.median() - 1), 1), "p_lose_half": round(100 * (G.end_mult <= .5).mean(), 1)})
    return rows


def main():
    t_start = time.time()
    C, Raw, V, acc = panel()
    R = C.pct_change(fill_method=None)
    R = R.where(R.abs() < 3)
    mr = acc.pct_change()
    me = pd.Series(C.index, index=C.index).groupby(C.index.str[:7]).last().values
    me = [d for d in me if d >= "2014-06-01"]
    pos = {d: C.index.get_loc(d) for d in me}
    ma50, ma200 = C.rolling(50, min_periods=40).mean(), C.rolling(200, min_periods=150).mean()
    rows = []
    for d0 in me:
        t0 = pos[d0]
        if t0 < 130:
            continue
        v60 = V.iloc[t0 - 59:t0 + 1].median()
        ok = (v60 >= 1e4) & (Raw.iloc[t0] >= 0.005) & C.iloc[t0].notna() & R.iloc[t0 - 59:t0 + 1].notna().sum().ge(30)
        codes = list(ok[ok].index)
        if len(codes) < int(os.environ.get("S66_MIN", "100")):
            continue
        c = C.iloc[t0][codes]
        X = pd.DataFrame(index=codes)
        X["log_price"] = np.log(Raw.iloc[t0][codes])
        X["log_value"] = np.log(v60[codes])
        X["value_surge"] = np.log((V.iloc[t0 - 19:t0 + 1][codes].median() + 1) / (V.iloc[max(0, t0 - 249):t0 + 1][codes].median() + 1))
        px = C.iloc[max(0, t0 - 755):t0 + 1][codes].ffill()
        X["ret_1m"] = c / px.iloc[-22] - 1
        X["ret_3m"] = c / px.iloc[-64] - 1
        X["ret_12m"] = c / px.iloc[max(-253, -len(px))] - 1
        X["from_high_3y"] = c / px.max() - 1
        X["from_low_1y"] = c / px.iloc[-252:].min() - 1
        w = R.iloc[t0 - 59:t0 + 1][codes]
        X["vol_60"] = w.std() * math.sqrt(252)
        X["range_60"] = px.iloc[-60:].max() / px.iloc[-60:].min() - 1
        X["above_ma50"] = c / ma50.iloc[t0][codes] - 1
        X["above_ma200"] = c / ma200.iloc[t0][codes] - 1
        X["zero_days_60"] = (V.iloc[t0 - 59:t0 + 1][codes] <= 0).sum()
        X["up_days_20"] = (R.iloc[t0 - 19:t0 + 1][codes] > 0).sum()
        X["max_day_20"] = R.iloc[t0 - 19:t0 + 1][codes].max()
        m = mr.iloc[t0 - 59:t0 + 1]
        X["beta_like"] = w.apply(lambda s: s.cov(m)) / m.var()
        seg = C.iloc[t0:t0 + H + 1][codes].ffill()
        X["peak_mult"] = seg.max() / c
        X["end_mult"] = seg.iloc[-1] / c
        X["complete"] = t0 + H < len(C)
        X["month"], X["t0"], X["code"] = d0[:7], t0, codes
        X["bench_12m"] = acc.iloc[min(t0 + H, len(acc) - 1)] / acc.iloc[t0] - 1
        rows.append(X.reset_index(drop=True))
    P = pd.concat(rows, ignore_index=True).replace([np.inf, -np.inf], np.nan)
    for k in (2, 5, 10):
        P[f"hit{k}"] = (P.peak_mult >= k).astype(int)
    D = P[P.complete]
    print("[panel]", len(P), "snapshots", P.code.nunique(), "stocks", f"{time.time() - t_start:.0f}s")
    out = {"method": __doc__.strip(), "snapshots": int(len(D)), "stocks": int(P.code.nunique())}
    out["base"] = {"p_2x": round(100 * D.hit2.mean(), 1), "p_5x": round(100 * D.hit5.mean(), 2), "p_10x": round(100 * D.hit10.mean(), 2),
                   "p_end_2x": round(100 * (D.end_mult >= 2).mean(), 1), "p_end_lose_half": round(100 * (D.end_mult <= .5).mean(), 1),
                   "mean_12m": round(100 * (D.end_mult.mean() - 1), 1), "median_12m": round(100 * (D.end_mult.median() - 1), 1)}
    out["tables"] = {
        "股价": bucket_table(D, "log_price", [-9, np.log(.02), np.log(.05), np.log(.2), np.log(1), np.log(5), 9],
                           ["<2 分", "2–5 分", "5–20 分", "20 分–$1", "$1–5", ">$5"]),
        "日成交额": bucket_table(D, "log_value", [0, np.log(5e4), np.log(2e5), np.log(1e6), np.log(5e6), 30],
                             ["<$5 万", "$5–20 万", "$20–100 万", "$100–500 万", ">$500 万"]),
        "离 3 年高点": bucket_table(D, "from_high_3y", [-1.01, -.9, -.75, -.5, -.25, -.05, .01], ["跌 90%+", "跌 75–90%", "跌 50–75%", "跌 25–50%", "跌 5–25%", "新高附近"]),
        "过去 1 个月涨幅": bucket_table(D, "ret_1m", [-1, -.2, 0, .2, .5, 1, 100], ["跌 20%+", "跌 0–20%", "涨 0–20%", "涨 20–50%", "涨 50–100%", "涨 100%+"]),
        "成交额放大（近 20 日 / 近 1 年）": bucket_table(D, "value_surge", [-20, np.log(.5), np.log(1), np.log(2), np.log(5), 20], ["缩量一半以上", "略缩", "放大 1–2 倍", "放大 2–5 倍", "放大 5 倍+"]),
        "站上 200 日线": bucket_table(D, "above_ma200", [-1, -.3, 0, .3, 1, 100], ["低 30%+", "低 0–30%", "高 0–30%", "高 30–100%", "高 100%+"]),
        "过去 12 个月涨幅": bucket_table(D, "ret_12m", [-1, -.5, 0, .5, 1, 100], ["跌 50%+", "跌 0–50%", "涨 0–50%", "涨 50–100%", "涨 100%+"]),
    }
    # episodes: earliest snapshot per stock where the next 12 months reached >= 5x (one per 18-month window)
    eps = []
    for code, G in D[D.hit5 == 1].sort_values("t0").groupby("code"):
        last = -10 ** 9
        for r in G.itertuples():
            if r.t0 - last > 380:
                eps.append(r)
                last = r.t0
    E = pd.DataFrame([e._asdict() for e in eps]) if eps else pd.DataFrame()
    if len(E):
        out["episodes_5x"] = {"count": int(len(E)), "median_start": {f: round(float(E[f].median()), 3) for f in FEATS},
                              "universe_median": {f: round(float(D[f].median()), 3) for f in FEATS},
                              "list": [{"code": r.code, "month": r.month, "price": round(math.exp(r.log_price), 4), "peak_x": round(r.peak_mult, 1),
                                        "end_x": round(r.end_mult, 1), "value_k": round(math.exp(r.log_value) / 1e3), "from_high_3y": round(100 * r.from_high_3y),
                                        "ret_1m": round(100 * r.ret_1m), "value_surge": round(math.exp(r.value_surge), 1)}
                                       for r in E.sort_values("peak_mult", ascending=False).head(60).itertuples()]}
    cases = {}
    for code in CASES:
        G = P[P.code == code].sort_values("t0")
        if len(G):
            best = G.loc[G.peak_mult.idxmax()]
            cases[code] = {"best_start": best.month, "peak_x": round(float(best.peak_mult), 1), "end_x": round(float(best.end_mult), 1),
                           "price": round(math.exp(best.log_price), 4), "value_k": round(math.exp(best.log_value) / 1e3),
                           "from_high_3y": round(100 * float(best.from_high_3y)), "ret_1m": round(100 * float(best.ret_1m)),
                           "value_surge": round(math.exp(float(best.value_surge)), 1), "above_ma200": round(100 * float(best.above_ma200))}
    out["cases"] = cases
    print("[tables]", f"{time.time() - t_start:.0f}s", json.dumps(out["base"]))

    # walk-forward model for "doubles within 12 months", refit yearly with a 12-month embargo
    import lightgbm as lgb
    params = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=300, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7)
    P["p_lgb"] = np.nan
    months = sorted(P.month.unique())
    t_of = P.groupby("month").t0.first()
    imp = pd.Series(0.0, index=FEATS)
    for k in range(0, len(months), 12):
        test = months[k:k + 12]
        tr = P[P.complete & (P.t0 + H + 5 < t_of[test[0]])]
        if len(tr) < int(os.environ.get("S66_MINTRAIN", "20000")):
            continue
        mdl = lgb.train(params, lgb.Dataset(tr[FEATS], tr.hit2), num_boost_round=300)
        imp += pd.Series(mdl.feature_importance("gain"), index=FEATS)
        te = P.month.isin(test)
        P.loc[te, "p_lgb"] = mdl.predict(P.loc[te, FEATS])
    T = P[P.p_lgb.notna() & P.complete]
    out["lgb_first_month"] = T.month.min() if len(T) else None
    out["lgb_importance"] = {k: round(float(v / imp.sum() * 100), 1) for k, v in imp.sort_values(ascending=False).items()} if imp.sum() else {}

    def cohort(sel, label):
        S = sel[sel.end_mult.notna()]
        net = S.end_mult * (1 - COST) - 1
        by_m = net.groupby(S.month).mean()
        return {"label": label, "n": int(len(S)), "p_2x_peak": round(100 * S.hit2.mean(), 1), "p_10x_peak": round(100 * S.hit10.mean(), 2),
                "p_end_2x": round(100 * (S.end_mult >= 2).mean(), 1), "p_end_lose_half": round(100 * (S.end_mult <= .5).mean(), 1),
                "mean_12m_net": round(100 * net.mean(), 1), "median_12m_net": round(100 * net.median(), 1),
                "mean_of_monthly_cohorts": round(100 * by_m.mean(), 1),
                "mean_12m_cost8": round(100 * (S.end_mult * 0.92 - 1).mean(), 1),
                "mean_12m_cap10x": round(100 * (S.end_mult.clip(upper=10) * (1 - COST) - 1).mean(), 1),
                "mean_12m_cap5x_cost8": round(100 * (S.end_mult.clip(upper=5) * 0.92 - 1).mean(), 1),
                "t_cohorts": round(float(by_m.mean() / (by_m.std() / math.sqrt(max(1, len(by_m) / 12)))), 2) if len(by_m) > 12 else None, "share_cohorts_positive": round(100 * (by_m > 0).mean()),
                "bench_12m": round(100 * S.groupby("month").bench_12m.first().mean(), 1),
                "by_period": {lab: round(100 * net[(S.month >= lo) & (S.month <= hi)].mean(), 1)
                              for lab, lo, hi in (("2016–21", "2016", "2021-12"), ("2022–23", "2022", "2023-12"), ("2024–25", "2024", "9999"))}}
    res = {}
    if len(T):
        res["all"] = cohort(T, "全部小股票（同期）")
        res["small"] = cohort(T[T.log_value < np.log(1e6)], "日成交 <$100 万 的全部")
        top = T.sort_values("p_lgb", ascending=False).groupby("month").head(30)
        res["lgb_top30"] = cohort(top, "模型每月前 30")
        top_s = T[T.log_value < np.log(1e6)].sort_values("p_lgb", ascending=False).groupby("month").head(30)
        res["lgb_top30_small"] = cohort(top_s, "模型每月前 30（只选日成交 <$100 万）")
        L = T[(T.log_price >= np.log(0.02)) & (T.log_value >= np.log(3e4))]
        top_l = L.sort_values("p_lgb", ascending=False).groupby("month").head(30)
        res["lgb_top30_tradable"] = cohort(top_l, "模型每月前 30（股价 ≥2 分、日成交 ≥$3 万）")
        res["all_tradable"] = cohort(L, "全部（股价 ≥2 分、日成交 ≥$3 万）")
        top20 = L.sort_values("p_lgb", ascending=False).groupby("month").head(20)
        res["lgb_top20_tradable"] = cohort(top20, "模型每月前 20（股价 ≥2 分、日成交 ≥$3 万）")
        # simple rule from the lift tables: >= 2x volume surge and price above the 200-day line, after a deep fall
        rule = T[(T.value_surge > np.log(2)) & (T.above_ma200 > 0) & (T.from_high_3y < -.5)]
        res["rule"] = cohort(rule, "规则：跌过 50% + 放量 2 倍 + 站上 200 日线")
    out["picking"] = res
    allrule = P[(P.value_surge > np.log(2)) & (P.above_ma200 > 0) & (P.from_high_3y < -.5)]
    out["rule_count_by_month"] = {m: int(n) for m, n in allrule.groupby("month").size().tail(30).items()}
    out["rule_latest"] = sorted(allrule[allrule.month == P.month.max()].code.tolist())
    # current candidates
    last = P[P.month == P.month.max()].copy()
    if len(T):
        tr = P[P.complete]
        mdl = lgb.train(params, lgb.Dataset(tr[FEATS], tr.hit2), num_boost_round=300)
        last["p_lgb"] = mdl.predict(last[FEATS])
        cand = last.nlargest(40, "p_lgb")
        out["candidates"] = [{"code": r.code, "prob_2x": round(100 * r.p_lgb, 1), "price": round(math.exp(r.log_price), 4),
                              "value_k": round(math.exp(r.log_value) / 1e3), "ret_1m": round(100 * r.ret_1m), "from_high_3y": round(100 * r.from_high_3y),
                              "value_surge": round(math.exp(r.value_surge), 1)} for r in cand.itertuples()]
        try:
            import volml
            for x in out["candidates"]:
                ci = volml.company_info(x["code"])
                x.update({"name": ci.get("displayName"), "sector": ci.get("sector"), "mcap": ci.get("marketCap"), "about": (ci.get("description") or "")[:200]})
        except Exception:
            pass
    out["as_of"] = C.index[-1]
    out["minutes"] = round((time.time() - t_start) / 60, 1)
    p = Path(os.environ.get("STUDY66_OUT", study.DATA / "study66_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[done]", json.dumps(res, ensure_ascii=False)[:2500])


if __name__ == "__main__":
    main()
