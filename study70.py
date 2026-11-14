"""Round 70: monthly MACD bullish divergence -- does a long divergence (price keeps falling while the monthly MACD
keeps rising) lead to a strong reversal? pbz's idea, tested on every ASX stock (delisted ones included), 2014 on.

Monthly bars = adjusted close at each month end. MACD(12,26,9) on monthly closes, two versions:
  raw  = EMA12 - EMA26 in dollars (what charting software shows)
  ppo  = (EMA12 - EMA26) / EMA26 (same thing in percent -- removes the effect of the share price shrinking)
Divergence measures at each month end t:
  yoy      price below 12 months ago AND indicator above 12 months ago; dur = consecutive months this holds
  twolows  lowest close of the last 12 months < lowest close of months 13-36 back, but the indicator at the new low is
           higher than at the old low, indicator still < 0
Universe: month ends with 60-day median value >= $10k/day, raw price >= 0.5c, >= 30 monthly bars.
Outcome over the next 12 months (252 sessions; delisted -> last traded price): peak multiple, end multiple.
Comparison group: stocks also below their price 12 months ago but without the divergence (same months).
Returns net of 3% round-trip cost, end multiple capped at 5x for the means (one lottery ticket can't carry it).
"""
import json
import math
import os
import time

import numpy as np
import pandas as pd

import study
import study66

H = 252
COST = 0.03


def macd_frames(M):
    e12 = M.ewm(span=12, adjust=False, min_periods=12).mean()
    e26 = M.ewm(span=26, adjust=False, min_periods=26).mean()
    raw = e12 - e26
    return {"raw": raw, "ppo": raw / e26}


def run_length(B):
    out = np.zeros(B.shape, dtype=int)
    a = B.values
    for i in range(len(a)):
        out[i] = np.where(a[i], (out[i - 1] if i else 0) + 1, 0)
    return pd.DataFrame(out, index=B.index, columns=B.columns)


def two_lows(M, I):
    res = pd.DataFrame(False, index=M.index, columns=M.columns)
    m, ind = M.values, I.values
    for t in range(36, len(M)):
        w2, w1 = m[t - 11:t + 1], m[t - 35:t - 11]
        ok = ~np.isnan(w2).all(0) & ~np.isnan(w1).all(0)
        if not ok.any():
            continue
        cols = np.where(ok)[0]
        a2 = np.nanargmin(w2[:, cols], 0)
        a1 = np.nanargmin(w1[:, cols], 0)
        l2, l1 = w2[a2, cols], w1[a1, cols]
        i2, i1 = ind[t - 11 + a2, cols], ind[t - 35 + a1, cols]
        res.iloc[t, cols] = (l2 < l1) & (i2 > i1) & (ind[t, cols] < 0)
    return res


def stats(S, label):
    if not len(S):
        return {"label": label, "n": 0}
    net = S.end_mult.clip(upper=5) * (1 - COST) - 1
    win = S[S.peak_mult >= 2]
    return {"label": label, "n": int(len(S)), "stocks": int(S.code.nunique()),
            "p_2x_peak": round(100 * (S.peak_mult >= 2).mean(), 1), "p_5x_peak": round(100 * (S.peak_mult >= 5).mean(), 2),
            "p_end_up": round(100 * (S.end_mult > 1).mean(), 1), "p_end_lose_half": round(100 * (S.end_mult <= .5).mean(), 1),
            "mean_12m_net_cap5": round(100 * net.mean(), 1), "median_12m": round(100 * (S.end_mult.median() - 1), 1),
            "peak_if_2x_median": round(float(win.peak_mult.median()), 1) if len(win) else None,
            "peak_if_2x_p90": round(float(win.peak_mult.quantile(.9)), 1) if len(win) else None}


def vs(S, C_, label):
    """per-month difference in capped net return, t-stat with overlap-adjusted month count"""
    a = (S.end_mult.clip(upper=5) * (1 - COST) - 1).groupby(S.month).mean()
    b = (C_.end_mult.clip(upper=5) * (1 - COST) - 1).groupby(C_.month).mean()
    d = (a - b).dropna()
    t = float(d.mean() / (d.std() / math.sqrt(max(1, len(d) / 12)))) if len(d) > 12 else None
    p2 = ((S.peak_mult >= 2).groupby(S.month).mean() - (C_.peak_mult >= 2).groupby(C_.month).mean()).dropna()
    return {"label": label, "months": int(len(d)), "extra_12m_net_cap5": round(100 * d.mean(), 1), "t": round(t, 2) if t else None,
            "extra_p_2x": round(100 * p2.mean(), 1), "share_months_better": round(100 * (d > 0).mean())}


def main():
    t_start = time.time()
    C, Raw, V, acc = study66.panel()
    me = list(pd.Series(C.index, index=C.index).groupby(C.index.str[:7]).last().values)
    pos = {d: C.index.get_loc(d) for d in me}
    Cf = C.ffill(limit=10)
    M = Cf.loc[me]
    M = M.where(M.notna().cumsum() >= 1)
    ind = macd_frames(M)
    falling = M < M.shift(12)
    feats = {}
    for k, I in ind.items():
        sig = I.ewm(span=9, adjust=False, min_periods=9).mean()
        feats[f"{k}_dur"] = run_length(falling & (I > I.shift(12)))
        feats[f"{k}_two"] = two_lows(M, I)
        feats[f"{k}_hist_pos_dur"] = run_length((I - sig) > 0)
        feats[f"{k}_val"] = I
    nbars = M.notna().cumsum()
    print("[macd]", f"{time.time() - t_start:.0f}s")

    rows = []
    for j, d0 in enumerate(me):
        t0 = pos[d0]
        if t0 < 130 or d0 < "2016-01-01":
            continue
        v60 = V.iloc[t0 - 59:t0 + 1].median()
        ok = (v60 >= 1e4) & (Raw.iloc[t0] >= 0.005) & C.iloc[t0].notna() & (nbars.iloc[j] >= 30)
        codes = list(ok[ok].index)
        if len(codes) < int(os.environ.get("S70_MIN", "50")):
            continue
        c = C.iloc[t0][codes]
        seg = C.iloc[t0:t0 + H + 1][codes].ffill()
        X = pd.DataFrame({"code": codes, "month": d0[:7], "price": Raw.iloc[t0][codes].values,
                          "value": v60[codes].values, "falling": falling.iloc[j][codes].values.astype(bool),
                          "from_high_3y": (c / Cf.iloc[max(0, t0 - 755):t0 + 1][codes].max() - 1).values,
                          "peak_mult": (seg.max() / c).values, "end_mult": (seg.iloc[-1] / c).values,
                          "complete": t0 + H < len(C)})
        for k, F in feats.items():
            X[k] = F.iloc[j][codes].values
        rows.append(X)
    P = pd.concat(rows, ignore_index=True).replace([np.inf, -np.inf], np.nan)
    D = P[P.complete & P.end_mult.notna()]
    print("[panel]", len(P), "snapshots", f"{time.time() - t_start:.0f}s")

    out = {"method": __doc__.strip(), "snapshots": int(len(D)), "stocks": int(D.code.nunique()),
           "first_month": D.month.min(), "last_month": D.month.max()}
    out["all"] = stats(D, "全部股票")
    F_ = D[D.falling]
    out["falling"] = stats(F_, "比 12 个月前跌的（对照组）")
    for k in ("raw", "ppo"):
        dur = F_[f"{k}_dur"]
        none = F_[dur == 0]
        tab, cmp_ = [], []
        for lo, hi, lab in ((1, 5, "背离 1–5 个月"), (6, 11, "背离 6–11 个月"), (12, 23, "背离 12–23 个月"), (24, 999, "背离 24 个月+")):
            S = F_[(dur >= lo) & (dur <= hi)]
            tab.append(stats(S, lab))
            if len(S):
                cmp_.append(vs(S, none, lab + " vs 没背离"))
        tab.insert(0, stats(none, "跌但没背离"))
        two = F_[F_[f"{k}_two"].astype(bool)]
        tab.append(stats(two, "两个低点背离（经典画法）"))
        if len(two):
            cmp_.append(vs(two, F_[~F_[f"{k}_two"].astype(bool)], "两个低点背离 vs 其他下跌股"))
        # like CND: penny, down >75% from 3y high
        pen = F_[(F_.price < 0.05) & (F_.from_high_3y < -0.75)]
        pd_ = pen[pen[f"{k}_dur"] >= 12]
        tab.append(stats(pen[pen[f"{k}_dur"] == 0], "<5 分、离高点跌 75%+、没背离"))
        tab.append(stats(pd_, "<5 分、离高点跌 75%+、背离 12 个月+"))
        if len(pd_):
            cmp_.append(vs(pd_, pen[pen[f"{k}_dur"] == 0], "像 CND 这类：背离 12 个月+ vs 没背离"))
        hp = F_[f"{k}_hist_pos_dur"]
        hnone = F_[hp == 0]
        for lo, hi, lab in ((1, 11, "柱子在零轴上方 1–11 个月"), (12, 23, "柱子在零轴上方 12–23 个月"), (24, 999, "柱子在零轴上方 24 个月+")):
            S = F_[(hp >= lo) & (hp <= hi)]
            tab.append(stats(S, lab + "（股价仍在跌）"))
            if len(S):
                cmp_.append(vs(S, hnone, lab + " vs 柱子在下方"))
        out[k] = {"table": tab, "compare": cmp_}

    cur = {}
    last_j = len(me) - 1
    for code in ("CND",) + tuple(os.environ.get("S70_CODES", "").split(",") if os.environ.get("S70_CODES") else ()):
        if code not in M.columns:
            continue
        hist = []
        for j in range(max(0, last_j - 48), last_j + 1):
            hist.append({"month": me[j][:7], "close": round(float(M.iloc[j][code]), 4) if pd.notna(M.iloc[j][code]) else None,
                         **{k: (round(float(feats[f"{k}_val"].iloc[j][code]), 5) if pd.notna(feats[f"{k}_val"].iloc[j][code]) else None) for k in ind}})
        cur[code] = {"raw_dur": int(feats["raw_dur"].iloc[last_j][code]), "ppo_dur": int(feats["ppo_dur"].iloc[last_j][code]),
                     "raw_two": bool(feats["raw_two"].iloc[last_j][code]), "ppo_two": bool(feats["ppo_two"].iloc[last_j][code]),
                     "raw_hist_pos_dur": int(feats["raw_hist_pos_dur"].iloc[last_j][code]),
                     "ppo_hist_pos_dur": int(feats["ppo_hist_pos_dur"].iloc[last_j][code]), "monthly": hist}
    out["current"] = cur
    # today's stocks with a long divergence (ppo version), tradable
    L = P[P.month == P.month.max()]
    L = L[L.falling & (L.ppo_dur >= 12) & (L.value >= 3e4)].sort_values("ppo_dur", ascending=False)
    out["today_long_divergence"] = [{"code": r.code, "price": round(r.price, 4), "ppo_dur": int(r.ppo_dur), "raw_dur": int(r.raw_dur),
                                     "from_high_3y": round(100 * r.from_high_3y), "value_k": round(r.value / 1e3)} for r in L.head(40).itertuples()]
    out["seconds"] = round(time.time() - t_start)
    path = os.environ.get("STUDY70_OUT", "study70_results.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=float)
    print(json.dumps({k: out[k] for k in ("all", "falling")}, ensure_ascii=False))
    for k in ("raw", "ppo"):
        for r in out[k]["table"]:
            print(k, r)
        for r in out[k]["compare"]:
            print(k, r)
    print(json.dumps({c: {kk: v for kk, v in d.items() if kk != "monthly"} for c, d in cur.items()}))


if __name__ == "__main__":
    main()
