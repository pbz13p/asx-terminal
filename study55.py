"""Round 55 (bold): a macro model that times the ASX 200 ETF, tested walk-forward.

Weekly features known at the time: AU 2y change (1m, 3m), US 10y change (1m), VIX level and 1m change,
market breadth, ASX 200 momentum (1m, 3m, 12m), drawdown from 1y high, AUD 1m, gold 1m, September/July
flags. Target: next-20-session ASX 200 accumulation return minus cash.
Model: ridge regression on standardised features, refit every year on all data before it (expanding),
first test year 2018. Positions: 100% when the forecast is above its training median, else 50% (and a
smooth version). Daily P&L on the accumulation index, idle cash at the RBA rate, 0.1% per turnover.
Compared with buy-and-hold for 2018-21, 2022-23, 2024+.
"""
import bisect
import json
import math
import os
from pathlib import Path

import numpy as np

import study
import study7
from study25 import stats
from study42 import breadth_series

NAMES = ["澳2年1月变化", "澳2年3月变化", "美10年1月变化", "VIX", "VIX1月变化", "市场宽度", "大盘1月", "大盘3月",
         "大盘12月", "离1年高点", "澳元1月", "金价1月", "9月", "7月"]


def main():
    study.download(["AXJOA.INDX", "AXJO.INDX", "VIX.INDX", "AUDUSD.FOREX", "XAUUSD.FOREX"])
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    px = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    vix = {r[0]: r[4] for r in study.load("VIX.INDX")}
    aud = {r[0]: r[4] for r in study.load("AUDUSD.FOREX")}
    gold = {r[0]: r[4] for r in study.load("XAUUSD.FOREX")}
    f2 = study7.rba_table("f2")["FCMYGBAG2D"]
    us10 = study7.fred("DGS10")
    f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
    days = [d for d in sorted(acc) if d in px and d >= "2014-01-01"]
    br = breadth_series(days)
    K = {k: sorted(v) for k, v in (("vix", vix), ("aud", aud), ("gold", gold), ("f2", f2), ("us", us10), ("f1", f1), ("br", br))}
    S = {"vix": vix, "aud": aud, "gold": gold, "f2": f2, "us": us10, "f1": f1, "br": br}

    def before(name, d, back=0, strict=True):
        ks = K[name]
        j = (bisect.bisect_left(ks, d) if strict else bisect.bisect_right(ks, d)) - 1 - back
        return S[name][ks[j]] if j >= 0 else None

    def chg(name, d, n, rel=False, strict=True):
        a, b = before(name, d, 0, strict), before(name, d, n, strict)
        if a is None or b is None:
            return None
        return a / b - 1 if rel else a - b
    X, y, wd = [], [], []
    for i in range(260, len(days) - 20, 5):
        d = days[i]
        c = [px[days[k]] for k in range(i - 252, i + 1)]
        f = [chg("f2", d, 21, strict=False), chg("f2", d, 63, strict=False), chg("us", d, 21), before("vix", d), chg("vix", d, 21),
             before("br", d, 0, False), c[-1] / c[-22] - 1, c[-1] / c[-64] - 1, c[-1] / c[0] - 1, c[-1] / max(c) - 1,
             chg("aud", d, 21, True), chg("gold", d, 21, True), 1.0 if d[5:7] == "09" else 0.0, 1.0 if d[5:7] == "07" else 0.0]
        if any(v is None for v in f):
            continue
        cash = (before("f1", d, 0, False) or 0) / 100 * 20 / 252
        X.append(f)
        y.append(acc[days[i + 20]] / acc[d] - 1 - cash)
        wd.append(d)
    X, y = np.array(X), np.array(y)
    pred, med = {}, {}
    coefs = None
    for year in range(2018, int(days[-1][:4]) + 1):
        tr = [k for k, d in enumerate(wd) if d < f"{year}-01-01" and wd.index(d) + 4 < len(wd) and wd[min(len(wd) - 1, k + 4)] < f"{year}-01-01"]
        te = [k for k, d in enumerate(wd) if d[:4] == str(year)]
        if len(tr) < 100 or not te:
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        Z = (X[tr] - mu) / sd
        lam = 10.0
        b = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y[tr] - y[tr].mean()))
        fit = Z @ b + y[tr].mean()
        m = float(np.median(fit))
        for k in te:
            pred[wd[k]] = float(((X[k] - mu) / sd) @ b + y[tr].mean())
            med[wd[k]] = m
        coefs = dict(zip(NAMES, [round(float(v), 4) for v in b]))
    # daily simulation with weekly signals
    pk = sorted(pred)
    out = {}
    for name, rule in (("二元：高于中位数 100%，否则 50%", lambda p, m: 1.0 if p > m else .5),
                       ("平滑：50%–100% 随预测调整", lambda p, m: max(.5, min(1.0, .75 + 10 * (p - m))))):
        rets, bh, dates, prev = [], [], [], None
        for i in range(1, len(days)):
            d, p = days[i], days[i - 1]
            j = bisect.bisect_right(pk, p) - 1
            if j < 0 or d < pk[0]:
                continue
            w = rule(pred[pk[j]], med[pk[j]])
            r = acc[d] / acc[p] - 1
            c = (before("f1", p, 0, False) or 0) / 100 / 252
            rets.append(w * r + (1 - w) * c - (.001 * abs(w - prev) if prev is not None else 0))
            bh.append(r)
            dates.append(d)
            prev = w
        res = {}
        for per, lo, hi in (("2018–21", "2018", "2022"), ("2022–23", "2022", "2024"), ("2024后", "2024", "9999"), ("全部", "2018", "9999")):
            idx = [k for k, d in enumerate(dates) if lo <= d < hi]
            if idx:
                a, b_ = stats([rets[k] for k in idx]), stats([bh[k] for k in idx])
                res[per] = {"model_cagr": round(a["cagr"], 2), "bh_cagr": round(b_["cagr"], 2), "model_dd": round(a["maxdd"], 1),
                            "bh_dd": round(b_["maxdd"], 1), "model_sharpe": round(a["sharpe"], 2), "bh_sharpe": round(b_["sharpe"], 2)}
        out[name] = res
    # forecast quality: correlation of prediction with outcome (weekly, out of sample)
    pr = [(pred[d], y[wd.index(d)]) for d in pk]
    a = np.array(pr)
    ic = float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]) if len(a) > 10 else None
    now_f = None
    out["_oos_corr"] = round(ic, 3) if ic is not None else None
    out["_last_coefs"] = coefs
    out["_latest_forecast"] = {"date": pk[-1], "pred_20d_excess_cash_pct": round(100 * pred[pk[-1]], 2), "median_pct": round(100 * med[pk[-1]], 2)}
    p = Path(os.environ.get("STUDY55_OUT", study.DATA / "study55_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
