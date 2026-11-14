"""Round 71: the common technical indicators, one by one and mixed -- do any of them predict ASX returns?

Every ASX stock in the cache (delisted ones included), 2015 on, sampled every 5 trading days. Universe at each
sample: 60-day median value >= $10k/day, raw price >= 0.5c. Two groups: "big" = top 200 by 60-day value that day,
"small" = the rest. Indicators use data up to that close only (adjusted prices).
Outcome: return over the next 5 / 20 / 60 sessions (delisted -> last traded price), minus the average of the same
group on the same day (= excess return). Trading cost per signal: 0.4% round trip (big) / 3% (small), 20-day hold.

Part A  textbook signals (RSI<30, KDJ golden cross, MACD cross, golden cross, Bollinger, ...): average excess
        return after the signal, hit rate, t-stat (dates as clusters, overlap-adjusted).
Part B  each indicator as a number: rank correlation with next-20-day excess return (IC), per group.
Part C  every pair of signals (A AND B): picked on 2015-2021 only, then checked on 2022 onwards (out of sample),
        so lucky pairs found by data-mining show up as failures.
Part D  all indicators together in LightGBM, walk-forward yearly with embargo: top 10% each sample date vs group.
"""
import itertools
import json
import math
import os
import time

import numpy as np
import pandas as pd

import study

HS = (5, 20, 60)
STEP = 5
COST = {"big": 0.004, "small": 0.03}


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def indicators(c, h, l, v):
    f = {}
    d = c.diff()
    up, dn = wilder(d.clip(lower=0), 14), wilder((-d).clip(lower=0), 14)
    f["rsi14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    e12, e26 = ema(c, 12), ema(c, 26)
    ppo = (e12 - e26) / e26
    sig = ema(ppo, 9)
    f["macd_ppo"], f["macd_hist"] = ppo, ppo - sig
    lo9, hi9 = l.rolling(9).min(), h.rolling(9).max()
    rsv = 100 * (c - lo9) / (hi9 - lo9).replace(0, np.nan)
    K = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    D = K.ewm(alpha=1 / 3, adjust=False).mean()
    f["kdj_k"], f["kdj_d"], f["kdj_j"] = K, D, 3 * K - 2 * D
    lo14, hi14 = l.rolling(14).min(), h.rolling(14).max()
    f["williams_r"] = -100 * (hi14 - c) / (hi14 - lo14).replace(0, np.nan)
    tp = (h + l + c) / 3
    f["cci20"] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True))
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std()
    f["boll_pctb"] = (c - (m20 - 2 * s20)) / (4 * s20).replace(0, np.nan)
    f["boll_width"] = 4 * s20 / m20
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr, 14)
    f["atr_pct"] = atr / c
    pdm = (h.diff()).where((h.diff() > -l.diff()) & (h.diff() > 0), 0.0)
    ndm = (-l.diff()).where((-l.diff() > h.diff()) & (-l.diff() > 0), 0.0)
    pdi, ndi = 100 * wilder(pdm, 14) / atr, 100 * wilder(ndm, 14) / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    f["adx14"], f["di_diff"] = wilder(dx, 14), pdi - ndi
    ma = {n: c.rolling(n, min_periods=int(n * .8)).mean() for n in (5, 6, 10, 20, 50, 60, 200)}
    f["ma5_20"], f["ma20_60"], f["ma50_200"] = ma[5] / ma[20] - 1, ma[20] / ma[60] - 1, ma[50] / ma[200] - 1
    f["above_ma20"], f["above_ma50"], f["above_ma200"] = c / ma[20] - 1, c / ma[50] - 1, c / ma[200] - 1
    f["bias6"] = c / ma[6] - 1
    f["ema12_26"] = e12 / e26 - 1
    f["roc10"], f["roc20"] = c / c.shift(10) - 1, c / c.shift(20) - 1
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1
    obv = (np.sign(d).fillna(0) * v).cumsum()
    f["obv_slope20"] = (obv - obv.shift(20)) / v.rolling(20).sum().replace(0, np.nan)
    mf = tp * v
    pos = mf.where(tp > tp.shift(), 0.0).rolling(14).sum()
    neg = mf.where(tp < tp.shift(), 0.0).rolling(14).sum()
    f["mfi14"] = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    f["vol_ratio"] = v.rolling(5).mean() / v.rolling(60).mean().replace(0, np.nan)
    f["psy12"] = 100 * (d > 0).rolling(12).mean()
    t3 = ema(ema(ema(c, 15), 15), 15)
    f["trix"] = t3.pct_change(fill_method=None)
    au = h.rolling(26).apply(np.argmax, raw=True)
    ad = l.rolling(26).apply(np.argmin, raw=True)
    f["aroon_osc"] = 100 * (au - ad) / 25
    hi20, lo20 = h.shift().rolling(20).max(), l.shift().rolling(20).min()
    f["donchian_pos"] = (c - lo20) / (hi20 - lo20).replace(0, np.nan)
    f["from_high_250"] = c / h.rolling(250, min_periods=200).max() - 1
    f["vol20"] = np.log(c).diff().rolling(20).std() * math.sqrt(252)
    f["bw_rank_250"] = f["boll_width"].rolling(250, min_periods=200).rank(pct=True)
    # helpers for crossings
    f["_k_prev"], f["_d_prev"] = K.shift(), D.shift()
    f["_hist_prev"] = f["macd_hist"].shift()
    f["_ma5_20_prev3"] = f["ma5_20"].shift(3)
    f["_ma50_200_prev5"] = f["ma50_200"].shift(5)
    f["_hi20"] = hi20
    f["_hi250"] = h.shift().rolling(250, min_periods=200).max()
    f["_ret1"] = d / c.shift()
    f["_ret20"] = f["roc20"]
    return pd.DataFrame(f)


SIGNALS = {
    "RSI<30 超卖": lambda X: X.rsi14 < 30,
    "RSI>70 超买": lambda X: X.rsi14 > 70,
    "RSI 50–70 强势区": lambda X: X.rsi14.between(50, 70),
    "KDJ 金叉（K 上穿 D）": lambda X: (X.kdj_k > X.kdj_d) & (X._k_prev <= X._d_prev),
    "KDJ 低位金叉（K<30）": lambda X: (X.kdj_k > X.kdj_d) & (X._k_prev <= X._d_prev) & (X.kdj_k < 30),
    "KDJ J<0 超卖": lambda X: X.kdj_j < 0,
    "KDJ J>100 超买": lambda X: X.kdj_j > 100,
    "MACD 金叉（柱子由负转正）": lambda X: (X.macd_hist > 0) & (X._hist_prev <= 0),
    "MACD 零轴下金叉": lambda X: (X.macd_hist > 0) & (X._hist_prev <= 0) & (X.macd_ppo < 0),
    "MACD 在零轴上且柱子为正": lambda X: (X.macd_ppo > 0) & (X.macd_hist > 0),
    "5 日线上穿 20 日线": lambda X: (X.ma5_20 > 0) & (X._ma5_20_prev3 <= 0),
    "50 日线上穿 200 日线（黄金交叉）": lambda X: (X.ma50_200 > 0) & (X._ma50_200_prev5 <= 0),
    "站上 200 日线": lambda X: X.above_ma200 > 0,
    "跌破 200 日线": lambda X: X.above_ma200 < 0,
    "多头排列（20>60, 50>200, 价>20）": lambda X: (X.ma20_60 > 0) & (X.ma50_200 > 0) & (X.above_ma20 > 0),
    "跌破布林下轨": lambda X: X.boll_pctb < 0,
    "突破布林上轨": lambda X: X.boll_pctb > 1,
    "布林收口（带宽一年最窄 10%）": lambda X: X.bw_rank_250 < 0.1,
    "CCI<-100": lambda X: X.cci20 < -100,
    "CCI>100": lambda X: X.cci20 > 100,
    "威廉指标<-80 超卖": lambda X: X.williams_r < -80,
    "ADX>25 且 +DI>-DI（强趋势向上）": lambda X: (X.adx14 > 25) & (X.di_diff > 0),
    "突破 20 日新高": lambda X: X._hi20.notna() & (X.close > X._hi20),
    "突破 250 日新高": lambda X: X._hi250.notna() & (X.close > X._hi250),
    "放量上涨（5 日量 > 2 倍 60 日量）": lambda X: (X.vol_ratio > 2) & (X._ret1 > 0),
    "OBV 上升但价格下跌（量价背离）": lambda X: (X.obv_slope20 > 0.1) & (X._ret20 < -0.05),
    "MFI<20 资金超卖": lambda X: X.mfi14 < 20,
    "乖离率 BIAS6<-10%": lambda X: X.bias6 < -0.10,
    "心理线 PSY<25": lambda X: X.psy12 < 25,
    "Aroon>50 趋势向上": lambda X: X.aroon_osc > 50,
    "20 日涨幅>20%": lambda X: X.roc20 > 0.2,
    "12 个月动量前 20%（同组）": None,  # filled per date from mom_12_1 rank
}
NUM = ["rsi14", "macd_ppo", "macd_hist", "kdj_k", "kdj_d", "kdj_j", "williams_r", "cci20", "boll_pctb", "boll_width", "atr_pct",
       "adx14", "di_diff", "ma5_20", "ma20_60", "ma50_200", "above_ma20", "above_ma50", "above_ma200", "bias6", "ema12_26", "roc10",
       "roc20", "mom_12_1", "obv_slope20", "mfi14", "vol_ratio", "psy12", "trix", "aroon_osc", "donchian_pos", "from_high_250", "vol20",
       "bw_rank_250"]


def build():
    acc = pd.Series({r[0]: r[4] for r in study.load("AXJOA.INDX")}).sort_index()
    cal = pd.Index(acc.index)
    samples = cal[cal >= "2015-01-01"][::STEP]
    hmax = max(HS)
    parts = []
    files = sorted(study.CACHE.glob("*.AU.json"))
    for i, fpath in enumerate(files):
        rows = study.load(fpath.name[:-5])
        if len(rows) < 260:
            continue
        idx = [r[0] for r in rows]
        df = pd.DataFrame({"o": [r[1] for r in rows], "h": [r[2] for r in rows], "l": [r[3] for r in rows], "c": [r[4] for r in rows],
                           "v": [r[5] for r in rows], "raw": [r[6] for r in rows]}, index=idx)
        df = df[~df.index.duplicated()].reindex(cal)
        traded = df.c.notna()
        if traded.sum() < 260:
            continue
        last = traded[traded].index[-1]
        c = df.c.ffill(limit=10)
        h = df.h.fillna(c).ffill(limit=10)
        l = df.l.fillna(c).ffill(limit=10)
        v = df.v.fillna(0)
        value = (df.raw.ffill(limit=10) * v).fillna(0)
        F = indicators(c, h, l, v)
        F["close"] = c
        F["raw"] = df.raw.ffill(limit=10)
        F["value60"] = value.rolling(60, min_periods=40).median()
        cl = df.c.ffill()  # delisted -> last traded price
        for hh in HS:
            F[f"fwd{hh}"] = cl.shift(-hh) / c - 1
        F["fwd_ok"] = np.arange(len(cal)) + hmax < len(cal)
        S = F.loc[F.index.isin(samples) & traded.reindex(cal).fillna(False).values & (F.index <= last)]
        S = S[(S.value60 >= 1e4) & (S.raw >= 0.005)]
        if len(S):
            S = S.assign(code=fpath.name[:-8], date=S.index)
            parts.append(S.reset_index(drop=True))
        if i % 500 == 0:
            print("[build]", i, len(files))
    P = pd.concat(parts, ignore_index=True).replace([np.inf, -np.inf], np.nan)
    P["rk"] = P.groupby("date").value60.rank(ascending=False)
    P["grp"] = np.where(P.rk <= 200, "big", "small")
    for hh in HS:
        P[f"ex{hh}"] = P[f"fwd{hh}"].clip(-1, 5) - P.groupby(["date", "grp"])[f"fwd{hh}"].transform(lambda s: s.clip(-1, 5).mean())
    P["mom_rank"] = P.groupby(["date", "grp"]).mom_12_1.rank(pct=True)
    return P


def sig_mask(P, name):
    fn = SIGNALS[name]
    if fn is None:
        return P.mom_rank > 0.8
    return fn(P).fillna(False).astype(bool)


def tstat(x, dates, h):
    m = x.groupby(dates).mean()
    if len(m) < 20:
        return None, len(m)
    eff = max(2, len(m) * STEP / h)
    return float(m.mean() / (m.std() / math.sqrt(eff))), len(m)


def evaluate(S, grp, h=20):
    if len(S) < 30:
        return None
    x = S[f"ex{h}"]
    t, nd = tstat(x, S.date, h)
    net = x - COST[grp] if h == 20 else x
    return {"n": int(len(S)), "dates": nd, "excess": round(100 * float(x.mean()), 2), "excess_net": round(100 * float(net.mean()), 2),
            "hit": round(100 * float((x > 0).mean()), 1), "t": round(t, 2) if t is not None else None}


def main():
    t0 = time.time()
    P = build()
    P = P[P.fwd_ok]
    print("[panel]", len(P), "rows", P.code.nunique(), "stocks", f"{time.time() - t0:.0f}s")
    out = {"method": __doc__.strip(), "rows": int(len(P)), "stocks": int(P.code.nunique()), "dates": int(P.date.nunique()),
           "first": P.date.min(), "last": P.date.max()}
    masks = {k: sig_mask(P, k) for k in SIGNALS}
    # Part A
    A = []
    for name, m in masks.items():
        row = {"signal": name}
        for g in ("big", "small"):
            sel = m & (P.grp == g)
            row[g] = {f"h{h}": evaluate(P[sel], g, h) for h in HS}
            row[g]["share_of_days"] = round(100 * float(sel.sum() / max(1, (P.grp == g).sum())), 1)
        A.append(row)
    out["A_signals"] = A
    print("[A]", f"{time.time() - t0:.0f}s")
    # Part B: IC per indicator
    B = []
    for col in NUM:
        row = {"indicator": col}
        for g in ("big", "small"):
            G = P[P.grp == g][["date", col, "ex20"]].dropna()
            ic = pd.Series({dt: s[col].rank().corr(s.ex20.rank()) for dt, s in G.groupby("date") if len(s) > int(os.environ.get("S71_MINIC", "20"))},
                           dtype=float).dropna()
            eff = max(2, len(ic) * STEP / 20)
            row[g] = {"ic": round(float(ic.mean()), 4) if len(ic) else None, "t": round(float(ic.mean() / (ic.std() / math.sqrt(eff))), 2) if len(ic) > 20 else None,
                      "share_pos": round(100 * float((ic > 0).mean())) if len(ic) else None}
        B.append(row)
    out["B_ic"] = sorted(B, key=lambda r: -abs(r["small"]["ic"] or 0))
    print("[B]", f"{time.time() - t0:.0f}s")
    # Part C: pairs, selected in-sample, checked out-of-sample
    ins = P.date < "2022-01-01"
    C = {}
    for g in ("big", "small"):
        gm = P.grp == g
        cand = []
        names = list(masks)
        for a, b in itertools.combinations(names, 2):
            m = masks[a] & masks[b] & gm
            if m.sum() < 300:
                continue
            ri = evaluate(P[m & ins], g)
            ro = evaluate(P[m & ~ins], g)
            if ri and ri["t"] is not None:
                cand.append({"pair": f"{a} + {b}", "in_sample": ri, "out_of_sample": ro})
        cand.sort(key=lambda r: -(r["in_sample"]["excess_net"] if r["in_sample"]["t"] and r["in_sample"]["t"] > 0 else -99))
        top = cand[:20]
        held = [r for r in top if r["out_of_sample"] and r["out_of_sample"]["excess_net"] > 0]
        C[g] = {"pairs_tested": len(cand), "top20_in_sample": top, "top20_still_positive_after_2022": len(held)}
    out["C_pairs"] = C
    print("[C]", f"{time.time() - t0:.0f}s")
    # Part D: LightGBM on all indicators
    import lightgbm as lgb
    feats = NUM + ["log_price", "log_value"]
    P["log_price"], P["log_value"] = np.log(P.raw), np.log(P.value60 + 1)
    params = dict(objective="regression", learning_rate=0.03, num_leaves=31, min_data_in_leaf=500, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7)
    P["pred"] = np.nan
    years = sorted(P.date.str[:4].unique())
    imp = pd.Series(0.0, index=feats)
    for y in years:
        tr = P[(P.date < f"{int(y) - 1}-11-01")]  # ~2 months embargo for the 60-day outcome
        if tr.date.nunique() < 150:
            continue
        mdl = lgb.train(params, lgb.Dataset(tr[feats], tr.ex20.clip(-.5, 1)), num_boost_round=300)
        imp += pd.Series(mdl.feature_importance("gain"), index=feats)
        te = P.date.str[:4] == y
        P.loc[te, "pred"] = mdl.predict(P.loc[te, feats])
    T = P[P.pred.notna()]
    D = {"first_year": T.date.min()[:4] if len(T) else None,
         "importance": {k: round(float(v / imp.sum() * 100), 1) for k, v in imp.sort_values(ascending=False).items()} if imp.sum() else {}}
    for g in ("big", "small"):
        G = T[T.grp == g]
        if not len(G):
            continue
        G = G.assign(q=G.groupby("date").pred.rank(pct=True))
        D[g] = {"top10": evaluate(G[G.q > .9], g), "bottom10": evaluate(G[G.q <= .1], g),
                "top10_by_year": {y: round(100 * float(G[(G.q > .9) & (G.date.str[:4] == y)].ex20.mean() - COST[g]), 2)
                                  for y in sorted(G.date.str[:4].unique())}}
    # realistic cost per round trip: brokerage 0.1% x2 + one ASX price step (buy at the ask, sell at the bid)
    def tick(p):
        return 0.001 if p < 0.10 else 0.005 if p < 2 else 0.01
    T = T.assign(cost=0.002 + T.raw.map(tick) / T.raw, q=T.groupby(["date", "grp"]).pred.rank(pct=True))
    bands = []
    for lo, hi, lab in ((0, .02, "<2 分"), (.02, .1, "2–10 分"), (.1, .5, "10–50 分"), (.5, 2, "$0.5–2"), (2, 1e9, ">$2")):
        G = T[(T.grp == "small") & (T.raw >= lo) & (T.raw < hi)]
        top = G[G.q > .9]
        if len(top) < 100:
            continue
        net = top.ex20 - top.cost
        bands.append({"band": lab, "n": int(len(top)), "cost_median": round(100 * float(top.cost.median()), 2),
                      "excess": round(100 * float(top.ex20.mean()), 2), "excess_net": round(100 * float(net.mean()), 2),
                      "t_gross": tstat(top.ex20, top.date, 20)[0] and round(tstat(top.ex20, top.date, 20)[0], 2),
                      "net_by_year": {y: round(100 * float(net[top.date.str[:4] == y].mean()), 2) for y in sorted(top.date.str[:4].unique())}})
    D["small_top10_by_price_real_cost"] = bands
    out["D_lgb"] = D
    out["seconds"] = round(time.time() - t0)
    path = os.environ.get("STUDY71_OUT", "study71_results.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=float)
    print(json.dumps(out["D_lgb"], ensure_ascii=False)[:3000])
    print("[done]", out["seconds"], "s")


if __name__ == "__main__":
    main()
