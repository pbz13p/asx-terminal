"""Round 77: every public formula alpha we can compute, tested on China A-shares (main board), then fed to the model.

Libraries (factors/catalog.json, 476 entries, each with its source): GTJA 191 (国泰君安 2017), WorldQuant 101
(Kakushadze 2016), Microsoft Qlib Alpha158 (MIT), plus classic academic anomalies and common broker (金工) factors.
Entries that need data we lack (industry classes, market cap, Fama-French factors, minute bars, financials) are kept
in the catalog as "skipped" with the reason.

Same set-up as round 72: EODHD daily prices incl. delisted stocks, main board only (60, 000-003), universe = traded in
the last 10 days and 60-day median value above the bottom 20%; big = top 1000 by value, small = the rest. Sampled every
20 trading days from 2010 (non-overlapping 20-day outcomes, so plain t-stats are honest). Outcome = 20-day return
(exit pushed past limit-down days) minus the group average; limit-up days can't be bought; cost = commission 0.08% x2
+ stamp 0.05% + fees + one RMB 0.01 tick.

Per factor and group: monthly rank IC; direction chosen before 2019 and judged 2019 onwards (out of sample): IC, t,
top-decile net excess, decile monotonicity. Then LightGBM walk-forward (retrained yearly): round-72 features vs all
formula alphas vs both, top 10% net excess per month.
"""
import gzip
import json
import math
import os
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

import factorlib as fl
import study71 as s71
from study72 import MAIN, load

CN = Path("data/cn")
STEP, H = 20, 20
SPLIT = "2019-01-01"
OUT = Path(os.environ.get("STUDY77_OUT", "out/study77_results.json"))


def bench(cal):
    key = os.environ.get("EODHD_API_KEY")
    try:
        with urllib.request.urlopen(f"https://eodhd.com/api/eod/000300.SHG?api_token={key}&fmt=json&from=2005-01-01", timeout=60) as r:
            rows = json.loads(r.read())
        s = pd.DataFrame(rows).set_index("date")
        c = s["close"].reindex(cal).ffill()
        o = s["open"].reindex(cal).fillna(c)
        if c.notna().mean() > 0.9:
            return c.values[:, None], o.values[:, None], "CSI300"
    except Exception as e:
        print("bench fetch failed", type(e).__name__)
    return None, None, "equal-weight market"


def build():
    files = [f for f in sorted(CN.glob("*.json.gz")) if f.name.startswith(MAIN) and f.name.count(".") == 3]
    cnt = {}
    for f in files[::7]:
        for r in json.loads(gzip.decompress(f.read_bytes())):
            cnt[r[0]] = cnt.get(r[0], 0) + 1
    cal = pd.Index(sorted(d for d, n in cnt.items() if n >= 20 and d >= "2008-01-01"))
    T = len(cal)
    cols, O, Hh, L, C, V, RAW, TRD = [], [], [], [], [], [], [], []
    base_rows = []
    samples = np.where(cal >= "2010-01-01")[0][::STEP]
    t0 = time.time()
    for i, f in enumerate(files):
        df = load(f)
        if df is None:
            continue
        df = df.reindex(cal)
        traded = df.c.notna()
        if traded.sum() < 260:
            continue
        c = df.c.ffill(limit=10)
        h, l, o = df.h.fillna(c).ffill(limit=10), df.l.fillna(c).ffill(limit=10), df.o.fillna(c).ffill(limit=10)
        v = df.v.fillna(0)
        cols.append(f.name[:-8])
        O.append(o.values); Hh.append(h.values); L.append(l.values); C.append(c.values); V.append(v.values)
        RAW.append(df.raw.ffill(limit=10).values); TRD.append(traded.values)
        F = s71.indicators(c, h, l, v)                       # round-72 features, only at sample dates
        F = F.iloc[samples].assign(ret5=(c / c.shift(5) - 1).iloc[samples].values)
        F["col"] = len(cols) - 1
        F["t"] = samples
        base_rows.append(F.reset_index(drop=True))
        if i % 500 == 0:
            print("[build]", i, len(files), f"{time.time() - t0:.0f}s", flush=True)
    st = lambda a: np.column_stack(a).astype("float64")
    B = {"open": st(O), "high": st(Hh), "low": st(L), "close": st(C), "volume": st(V), "rawclose": st(RAW)}
    traded = np.column_stack(TRD)
    base = pd.concat(base_rows, ignore_index=True)
    return cal, cols, B, traded, base, samples


def outcomes(cal, B, traded, samples):
    c, raw, v = B["close"], B["rawclose"], B["volume"]
    T, N = c.shape
    r1 = np.full_like(c, np.nan)
    r1[1:] = c[1:] / c[:-1] - 1
    r1[~traded] = np.nan
    ld = np.nan_to_num(r1) <= -0.097
    lu = np.nan_to_num(r1) >= 0.097
    value60 = pd.DataFrame(raw * v).rolling(60, min_periods=40).median().values
    tr10 = pd.DataFrame(traded.astype(float)).rolling(10).sum().values > 0
    rows = []
    for t in samples:
        e = t + H
        if e >= T:
            continue
        ex = np.full(N, e)
        for _ in range(10):
            push = (ex < T - 1) & ld[np.minimum(ex, T - 1), np.arange(N)]
            if not push.any():
                break
            ex = ex + push
        cl = pd.DataFrame(c).ffill().values
        fwd = cl[ex, np.arange(N)] / c[t] - 1
        ok = traded[t] & tr10[t] & np.isfinite(fwd) & np.isfinite(value60[t])
        idx = np.where(ok)[0]
        rows.append(pd.DataFrame({"t": t, "col": idx, "fwd20": fwd[idx], "value60": value60[t, idx], "raw": raw[t, idx],
                                  "limit_up_today": lu[t, idx]}))
    P = pd.concat(rows, ignore_index=True)
    P["date"] = cal[P.t.values]
    P = P[P.groupby("date").value60.rank(pct=True) > 0.2]
    P["rk"] = P.groupby("date").value60.rank(ascending=False)
    P["grp"] = np.where(P.rk <= 1000, "big", "small")
    P["ex20"] = P.fwd20.clip(-1, 5) - P.groupby(["date", "grp"]).fwd20.transform(lambda s: s.clip(-1, 5).mean())
    P["cost"] = 0.0016 + 0.0005 + 0.0002 + 0.01 / P.raw
    return P.reset_index(drop=True)


def ic_series(x, y, dates):
    df = pd.DataFrame({"x": x, "y": y, "d": dates}).dropna()
    return df.groupby("d").apply(lambda s: s.x.rank().corr(s.y.rank()) if len(s) > 30 else np.nan, include_groups=False).dropna()


def tstat(s):
    return float(s.mean() / (s.std() / math.sqrt(len(s)))) if len(s) > 10 and s.std() > 0 else None


def judge(P, x, g):
    m = (P.grp == g).values & np.isfinite(x)
    if m.sum() < 5000:
        return None
    S = P.loc[m, ["date", "ex20", "cost", "limit_up_today"]].assign(x=x[m])
    ins = S.date < SPLIT
    ic_in = ic_series(S.x[ins], S.ex20[ins], S.date[ins])
    ic_out = ic_series(S.x[~ins], S.ex20[~ins], S.date[~ins])
    if len(ic_in) < 24 or len(ic_out) < 24:
        return None
    sign = 1 if ic_in.mean() >= 0 else -1
    O = S[~ins].copy()
    O["q"] = (sign * O.x).groupby(O.date).rank(pct=True)
    top = O[(O.q > 0.9) & ~O.limit_up_today.astype(bool)]
    per_month = (top.ex20 - top.cost).groupby(top.date).mean()
    dec = O.assign(dq=np.ceil(O.q * 10).clip(1, 10)).groupby("dq").ex20.mean()
    return {"ic_in": round(float(ic_in.mean()), 4), "t_in": round(tstat(ic_in) or 0, 2), "sign": sign,
            "ic_out": round(float(sign * ic_out.mean()), 4), "t_out": round(sign * (tstat(ic_out) or 0), 2),
            "top10_net_out": round(100 * float(per_month.mean()), 2), "top10_t_out": round(tstat(per_month) or 0, 2),
            "mono_out": round(float(pd.Series(dec.values).corr(pd.Series(range(len(dec))), method="spearman")), 2),
            "months_out": int(len(ic_out))}


def lgb_eval(P, X, feats, label):
    import lightgbm as lgb
    params = dict(objective="regression", learning_rate=0.03, num_leaves=31, min_data_in_leaf=1000, feature_fraction=0.5,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7, num_threads=4)
    pred = np.full(len(P), np.nan)
    imp = pd.Series(0.0, index=feats)
    years = sorted(P.date.str[:4].unique())
    for y in years:
        tr = (P.date < f"{int(y) - 1}-12-01").values
        if P.date[tr].nunique() < 36:
            continue
        mdl = lgb.train(params, lgb.Dataset(X[tr], P.ex20[tr].clip(-.5, 1).values, free_raw_data=True), num_boost_round=250)
        imp += pd.Series(mdl.feature_importance("gain"), index=feats)
        te = (P.date.str[:4] == y).values
        pred[te] = mdl.predict(X[te])
    out = {"model": label, "n_features": len(feats)}
    T = P.assign(pred=pred)
    T = T[T.pred.notna() & ~T.limit_up_today.astype(bool)]
    for g in ("big", "small"):
        G = T[T.grp == g]
        G = G.assign(q=G.groupby("date").pred.rank(pct=True))
        top = G[G.q > .9]
        pm = (top.ex20 - top.cost).groupby(top.date).mean()
        oos = pm[pm.index >= SPLIT]
        out[g] = {"top10_net_month": round(100 * float(pm.mean()), 2), "t": round(tstat(pm) or 0, 2),
                  "top10_net_month_2019on": round(100 * float(oos.mean()), 2), "t_2019on": round(tstat(oos) or 0, 2),
                  "by_year": {y: round(100 * float(v), 2) for y, v in pm.groupby(pm.index.str[:4]).mean().items()},
                  "positive_years": f"{int((pm.groupby(pm.index.str[:4]).mean() > 0).sum())}/{pm.index.str[:4].nunique()}"}
    out["top_features"] = {k: round(float(v / imp.sum() * 100), 1) for k, v in imp.sort_values(ascending=False).head(25).items()} if imp.sum() else {}
    return out


def main():
    t0 = time.time()
    cal, cols, B, traded, base, samples = build()
    bc, bo, bname = bench(cal)
    if bc is None:
        r = np.nanmean(B["close"][1:] / B["close"][:-1] - 1, axis=1)
        bc = np.concatenate([[1.0], np.cumprod(1 + np.nan_to_num(r))])[:, None]
        bo = bc
    B["bench_close"], B["bench_open"] = bc, bo
    print("[panel]", B["close"].shape, "bench", bname, f"{time.time() - t0:.0f}s", flush=True)
    P = outcomes(cal, B, traded, samples)
    P = P.merge(base, on=["t", "col"], how="left")
    print("[rows]", len(P), "dates", P.date.nunique(), f"{time.time() - t0:.0f}s", flush=True)
    cat = json.loads(Path("factors/catalog.json").read_text())
    E = fl.Engine(B)
    tt, cc = P.t.values, P.col.values
    X = np.full((len(P), len(cat)), np.nan, dtype="float32")
    done, results = [], []
    for k, e in enumerate(cat):
        r = {"id": e["id"], "lib": e["lib"], "source": e["source"], "formula": e.get("formula")}
        if not e.get("formula"):
            r.update(status="skipped", reason=e.get("reason"))
            results.append(r)
            continue
        try:
            v = E.run(e["formula"])
            x = v[tt, cc]
            if np.isfinite(x).mean() < 0.3:
                raise fl.Unsupported("mostly empty on real data")
            X[:, k] = x
            done.append(k)
            r["status"] = "ok"
            r["big"], r["small"] = judge(P, x, "big"), judge(P, x, "small")
        except Exception as ex:
            r.update(status="skipped", reason=f"{type(ex).__name__}: {ex}"[:120])
        results.append(r)
        if k % 25 == 0:
            print("[factor]", k, len(cat), e["id"], f"{time.time() - t0:.0f}s", flush=True)
        if len(E.cache) > 40:
            E.cache = {kk: vv for kk, vv in E.cache.items() if kk in ("vwap", "amount", "returns")}
    del E, B
    ok = [r for r in results if r["status"] == "ok"]
    summary = {}
    for g in ("big", "small"):
        J = [(r["id"], r[g]) for r in ok if r.get(g)]
        summary[g] = {"tested": len(J),
                      "ic_t_out_gt2": sum(1 for _, j in J if j["t_out"] > 2),
                      "top10_net_positive_t_gt2": sum(1 for _, j in J if j["top10_net_out"] > 0 and j["top10_t_out"] > 2),
                      "sign_flipped_out": sum(1 for _, j in J if j["t_in"] > 2 and j["t_out"] < 0),
                      "best_out": sorted([{"id": i, **j} for i, j in J], key=lambda z: -z["t_out"])[:30],
                      "by_lib": {lib: {"n": sum(1 for r in ok if r["lib"] == lib and r.get(g)),
                                       "t_out_gt2": sum(1 for r in ok if r["lib"] == lib and r.get(g) and r[g]["t_out"] > 2)}
                                 for lib in sorted({r["lib"] for r in ok})}}
    print("[factors done]", len(ok), "ok", f"{time.time() - t0:.0f}s", json.dumps({g: {k: v for k, v in s.items() if k != "best_out"} for g, s in summary.items()}, ensure_ascii=False), flush=True)
    base_feats = s71.NUM + ["ret5", "log_price", "log_value"]
    P["log_price"], P["log_value"] = np.log(P.raw), np.log(P.value60 + 1)
    XB = P[base_feats].values.astype("float32")
    names = [cat[k]["id"] for k in done]
    XA = X[:, done]
    models = [lgb_eval(P, XB, base_feats, "round72 features (34 textbook indicators)")]
    print("[lgb base]", json.dumps(models[-1], ensure_ascii=False)[:600], f"{time.time() - t0:.0f}s", flush=True)
    models.append(lgb_eval(P, XA, names, "all formula alphas"))
    print("[lgb alphas]", json.dumps(models[-1], ensure_ascii=False)[:600], f"{time.time() - t0:.0f}s", flush=True)
    models.append(lgb_eval(P, np.hstack([XB, XA]), base_feats + names, "round72 features + all formula alphas"))
    print("[lgb both]", json.dumps(models[-1], ensure_ascii=False)[:600], f"{time.time() - t0:.0f}s", flush=True)
    out = {"method": __doc__.strip(), "bench": bname, "rows": int(len(P)), "stocks": int(P.col.nunique()), "dates": int(P.date.nunique()),
           "first": P.date.min(), "last": P.date.max(), "catalog": len(cat), "computed": len(ok), "summary": summary,
           "models": models, "factors": results, "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("[done]", out["seconds"], "s")


if __name__ == "__main__":
    main()
