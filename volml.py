"""波动率学习 (volatility learning): forecast each stock's realised volatility over the next 5 and 20 sessions.
Direction is NOT predicted.

Stages (each can be run and tested on its own):
  data      price history (EODHD, >= 5 years) + announcements -> cleaned frames, cleaning log
  features  features known at each close (no look-ahead) + targets
  models    walk-forward: naive (past-20-day vol) -> GARCH(1,1) -> HAR-RV -> LightGBM, each kept only if it beats
            the one before it; metrics RMSE / QLIKE / correlation, by size and by announcement periods
  all       everything, writes volml_results.json for the app

Usage: python volml.py [--codes BHP,CBA,...] [--n 200] [--stage all]
"""
import argparse
import json
import math
import os
import re
import time
import urllib.request
import warnings
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import study

warnings.filterwarnings("ignore")
ANN = 252
HORIZONS = (5, 20)
YEARS = 7
LIST_URL = "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode={c}&timeframe=Y&year={y}"
EVENT_WORDS = {
    "results": ("appendix 4d", "appendix 4e", "half year", "half-year", "full year", "full-year", "annual report",
                "preliminary final", "interim result", "financial report"),
    "halt": ("trading halt", "suspension from quotation", "voluntary suspension"),
    "raising": ("placement", "capital raising", "entitlement offer", "share purchase plan", "rights issue", "institutional offer"),
    "mna": ("scheme implementation", "scheme booklet", "takeover", "bidder's statement", "bidders statement",
            "target's statement", "non-binding", "indicative proposal", "merger", "acquisition of"),
}
FEATURES = ["r", "log_sd5", "log_sd20", "log_sd60", "log_park20", "log_gk20", "log_atr14", "log_rv1",
            "vol_rel20", "log_value", "value_rel20",
            "days_to_results_est", "days_since_results", "days_since_halt", "days_since_raising", "days_since_mna",
            "after_reopen5", "log_mkt_sd5", "log_mkt_sd20", "mkt_r", "beta60", "log_garch_h", "log_har_h"]
STATS = Counter()
_calls = Counter()


# ================================================================ stage 1: data
def universe(n, extra):
    cands = []
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 800 or rows[-1][0] < (date.today() - timedelta(days=10)).isoformat():
            continue
        t = sorted(r[6] * r[5] for r in rows[-60:])
        cands.append((t[30], f.name[:-8]))
    cands.sort(reverse=True)
    codes = [c for _, c in cands[:n]]
    return codes + [c for c in extra if c not in codes]


def ensure_prices(codes):
    need = [f"{c}.AU" for c in codes] + ["AXJO.INDX"]
    before = sum(1 for s in need if not (study.CACHE / f"{s}.json").exists())
    study.download(need)
    _calls["eodhd_eod"] += before


def announcements(code, years):
    """[(date, text_lower)] from asx.com.au yearly lists (cached; current year refreshed daily). Empty if blocked."""
    out = []
    for y in years:
        f = study.CACHE / f"ann_{code}_{y}.json"
        if f.exists() and (y < date.today().year or time.time() - f.stat().st_mtime < 20 * 3600):
            out += json.loads(f.read_text(encoding="utf-8"))
            continue
        try:
            req = urllib.request.Request(LIST_URL.format(c=code, y=y), headers={"Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=40) as r:
                html = r.read().decode("utf-8", "replace")
            _calls["asx_announcements"] += 1
            items = []
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
                m = re.search(r"(\d{2})/(\d{2})/(\d{4})", tr)
                if m:
                    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tr)).strip().lower()
                    items.append([f"{m.group(3)}-{m.group(2)}-{m.group(1)}", text[:200]])
            f.write_text(json.dumps(items), encoding="utf-8")
            out += items
            time.sleep(0.25)
        except Exception:
            STATS["announcement_fetch_failed"] += 1
    return sorted(out)


def clean(code, start, calendar, log):
    """Cleaned daily frame. Rules (counted in `log`):
    1 drop rows with missing/zero close or adjusted close
    2 dividend/split/consolidation adjustment: O/H/L scaled by adjusted_close/close (EODHD adjusted series)
    3 repair inconsistent bars: high = max(o,h,c), low = min(o,l,c)
    4 zero-volume days are treated as halted: price is stale, no return computed for them
    5 sessions missing versus the ASX 200 calendar are counted as halts (gaps of 1-10 sessions)
    6 the first traded day after a halt keeps its return (it carries the whole halt gap) and is flagged
    7 returns beyond +/-50% are kept (real ASX events) but flagged; beyond +90%/-90% are set to missing (likely unadjusted corporate action)
    """
    raw = []
    f = study.CACHE / f"{code}.AU.json"
    if not f.exists():
        log["missing_file"] += 1
        return None
    for d, o, h, l, c, a, v in json.loads(f.read_text(encoding="utf-8")):
        if d < start:
            continue
        if not c or c <= 0 or not a or a <= 0:
            log["r1_dropped_bad_close"] += 1
            continue
        k = a / c
        o, h, l = (o or c) * k, (h or c) * k, (l or c) * k
        hh, ll = max(o, h, a), min(o, l, a)
        if hh != h or ll != l:
            log["r3_repaired_bars"] += 1
        raw.append((d, o, hh, ll, a, v or 0, c))
    if len(raw) < 300:
        log["too_short"] += 1
        return None
    df = pd.DataFrame(raw, columns=["date", "o", "h", "l", "c", "v", "close_raw"]).set_index("date")
    if (df["c"] != df["close_raw"]).any():
        log["r2_adjusted_stocks"] += 1
    df["halted"] = (df.v <= 0).astype(int)
    log["r4_zero_volume_days"] += int(df.halted.sum())
    pos = pd.Series(range(len(calendar)), index=calendar)
    p = pos.reindex(df.index)
    gap = (p - p.shift()).fillna(1) - 1
    df["gap_sessions"] = gap.clip(lower=0)
    log["r5_missing_sessions"] += int(df.gap_sessions[(df.gap_sessions >= 1) & (df.gap_sessions <= 10)].sum())
    traded = df[df.halted == 0].copy()
    r = np.log(traded.c).diff()
    prev_halt = (df.halted.shift(1).fillna(0) > 0).reindex(traded.index) | (traded.gap_sessions >= 1)
    traded["reopen"] = prev_halt.astype(int)
    log["r6_reopen_days"] += int(traded.reopen.sum())
    big = r.abs() > math.log(1.5)
    log["r7_flagged_over_50pct"] += int(big.sum())
    crazy = (r > math.log(1.9)) | (r < math.log(0.1))
    log["r7_set_missing_over_90pct"] += int(crazy.sum())
    r[crazy] = np.nan
    traded["r"] = r
    df = df.join(traded[["r", "reopen"]])
    df["reopen"] = df["reopen"].fillna(0)
    return df


# ================================================================ stage 2: features
def days_since(event_dates, idx):
    ev = sorted(set(event_dates))
    out, j = [], -1
    for d in idx:
        while j + 1 < len(ev) and ev[j + 1] <= d:
            j += 1
        out.append(min(400, (date.fromisoformat(d) - date.fromisoformat(ev[j])).days) if j >= 0 else 400)
    return np.array(out, dtype=float)


def build_features(df, mkt, anns):
    F = pd.DataFrame(index=df.index)
    r = df["r"]
    r2 = r ** 2
    F["r"] = r
    F["rv1"] = r2
    F["log_rv1"] = np.log(r2 + 1e-8)
    for n in (5, 20, 60):
        F[f"sd{n}"] = r.rolling(n, min_periods=int(n * .7)).std()
        F[f"log_sd{n}"] = np.log(F[f"sd{n}"] + 1e-6)
    F["rv5"] = r2.rolling(5, min_periods=4).mean()
    F["rv22"] = r2.rolling(22, min_periods=16).mean()
    F["rv20"] = r2.rolling(20, min_periods=14).mean()
    hl = np.log(df.h / df.l)
    co = np.log(df.c / df.o)
    F["log_park20"] = np.log((hl ** 2).rolling(20, min_periods=14).mean() / (4 * math.log(2)) + 1e-8)
    gk = 0.5 * hl ** 2 - (2 * math.log(2) - 1) * co ** 2
    F["log_gk20"] = np.log(gk.clip(lower=0).rolling(20, min_periods=14).mean() + 1e-8)
    tr = pd.concat([df.h - df.l, (df.h - df.c.shift()).abs(), (df.l - df.c.shift()).abs()], axis=1).max(axis=1)
    F["log_atr14"] = np.log(tr.rolling(14, min_periods=10).mean() / df.c + 1e-8)
    lv = np.log(df.v.where(df.v > 0))
    F["vol_rel20"] = lv - lv.rolling(20, min_periods=10).mean()
    val = np.log((df.v * df.close_raw).where(df.v > 0))
    F["log_value"] = val.rolling(20, min_periods=10).median()
    F["value_rel20"] = val - F["log_value"]
    idx = list(df.index)
    ev = {k: [d for d, t in anns if any(w in t for w in ws)] for k, ws in EVENT_WORDS.items()}
    F["days_since_results"] = days_since(ev["results"], idx)
    # next results estimated from the reporting cycle already observed (last results + ~182 days): no look-ahead
    F["days_to_results_est"] = np.where(F["days_since_results"] < 400, np.abs(182 - F["days_since_results"]), 400)
    F["days_since_halt"] = days_since(ev["halt"], idx)
    F["days_since_raising"] = days_since(ev["raising"], idx)
    F["days_since_mna"] = days_since(ev["mna"], idx)
    F["after_reopen5"] = df["reopen"].rolling(5, min_periods=1).max()
    m = mkt.reindex(df.index).ffill()
    F["mkt_r"] = m["r"]
    F["log_mkt_sd5"] = m["log_sd5"]
    F["log_mkt_sd20"] = m["log_sd20"]
    cov = (r * m["r"]).rolling(60, min_periods=40).mean() - r.rolling(60, min_periods=40).mean() * m["r"].rolling(60, min_periods=40).mean()
    F["beta60"] = cov / m["r"].rolling(60, min_periods=40).var()
    # targets: realised variance (sum of r^2) over the next h traded sessions, and an event flag for reporting
    any_ev = sorted(set(ev["results"] + ev["halt"] + ev["raising"] + ev["mna"]))
    evd = pd.Series(0, index=df.index)
    if any_ev:
        evd.loc[[d for d in any_ev if d in evd.index]] = 1
    for h in HORIZONS:
        s = r2.fillna(r2.rolling(20, min_periods=5).mean())
        F[f"y{h}"] = s[::-1].rolling(h).sum()[::-1].shift(-1)
        F[f"ev{h}"] = evd[::-1].rolling(h, min_periods=1).max()[::-1].shift(-1).fillna(0)
    return F


# ================================================================ stage 3: models
def garch_path(r, first_i, horizons, refit_every=21, window=750):
    """GARCH(1,1) refit monthly on the last `window` returns known at the refit date; forecasts for each day use
    parameters fitted strictly before it and the variance filtered through that day's close."""
    from arch import arch_model
    s = r.dropna() * 100
    dates, x = list(s.index), s.values
    out = {h: pd.Series(np.nan, index=r.index) for h in horizons}
    hmax = max(horizons)
    i = max(first_i, 300)
    while i < len(x):
        lo = max(0, i - window)
        seg = x[lo:min(len(x), i + refit_every)]
        try:
            am = arch_model(seg, vol="GARCH", p=1, q=1, mean="Constant", dist="normal", rescale=False)
            res = am.fit(last_obs=i - lo, disp="off", show_warning=False)
            fc = res.forecast(horizon=hmax, start=i - lo, reindex=False).variance.values
            for h in horizons:
                v = fc[:, :h].sum(axis=1) / 1e4
                out[h].loc[dates[i:i + len(v)]] = v
        except Exception:
            STATS["garch_fit_failed"] += 1
        i += refit_every
    return out


def qlike(y, f):
    y, f = np.maximum(y, 1e-10), np.maximum(f, 1e-10)
    return float(np.mean(y / f - np.log(y / f) - 1))


def to_vol(v, h):
    return np.sqrt(np.maximum(v, 0) / h * ANN) * 100


def metrics(y, f, h):
    a, b = to_vol(y, h), to_vol(f, h)
    return {"rmse": round(float(np.sqrt(np.mean((a - b) ** 2))), 2), "qlike": round(qlike(y, f), 4),
            "corr": round(float(np.corrcoef(a, b)[0, 1]), 3) if len(a) > 2 else None, "n": int(len(a))}


def quarter_starts(dates, first):
    qs = sorted({d[:5] + {"01": "01", "02": "01", "03": "01", "04": "04", "05": "04", "06": "04", "07": "07", "08": "07",
                         "09": "07", "10": "10", "11": "10", "12": "10"}[d[5:7]] for d in dates if d >= first})
    return [q + "-01" for q in qs]


def walk_forward_pooled(P, fit_fn, pred_fn, h, first):
    """Refit every quarter on rows whose h-day target ended before the quarter (embargo h+3 sessions ~ calendar)."""
    pred = pd.Series(np.nan, index=P.index)
    qs = quarter_starts(P.date.values, first)
    for k, q in enumerate(qs):
        end = qs[k + 1] if k + 1 < len(qs) else "9999"
        cut = (date.fromisoformat(q) - timedelta(days=int(h * 1.5) + 5)).isoformat()
        tr = P[(P.date < cut) & P[f"y{h}"].notna()]
        te = (P.date >= q) & (P.date < end)
        if len(tr) < 3000 or not te.any():
            continue
        mdl = fit_fn(tr)
        pred[te] = pred_fn(mdl, P[te])
    return pred


def har_fit(h):
    def fit(tr):
        X = np.column_stack([np.ones(len(tr)), np.log(tr.rv1 + 1e-8), np.log(tr.rv5 + 1e-8), np.log(tr.rv22 + 1e-8)])
        y = np.log(tr[f"y{h}"] + 1e-10)
        ok = np.isfinite(X).all(1) & np.isfinite(y)
        b, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
        s2 = float(np.var(y[ok] - X[ok] @ b))
        return b, s2

    def pred(m, te):
        b, s2 = m
        X = np.column_stack([np.ones(len(te)), np.log(te.rv1 + 1e-8), np.log(te.rv5 + 1e-8), np.log(te.rv22 + 1e-8)])
        return np.exp(X @ b + s2 / 2)
    return fit, pred


def lgb_fit(h, feats):
    import lightgbm as lgb
    params = dict(objective="regression", learning_rate=0.03, num_leaves=31, min_data_in_leaf=300, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7)

    def fit(tr):
        y = np.log(tr[f"y{h}"] + 1e-10)
        m = lgb.train(params, lgb.Dataset(tr[feats], y), num_boost_round=350)
        s2 = float(np.var(y - m.predict(tr[feats])))
        return m, s2

    def pred(m, te):
        mdl, s2 = m
        return np.exp(mdl.predict(te[feats]) + s2 / 2)
    return fit, pred


# ================================================================ orchestration
def run(codes, out_path, stage="all"):
    t0 = time.time()
    start = (date.today() - timedelta(days=int(365.25 * YEARS) + 150)).isoformat()
    years = list(range(int(start[:4]), date.today().year + 1))
    ensure_prices(codes)
    log = Counter()
    xjo = [r for r in study.load("AXJO.INDX") if r[0] >= start]
    calendar = [r[0] for r in xjo]
    xc = pd.Series([r[4] for r in xjo], index=calendar)
    mr = np.log(xc).diff()
    mkt = pd.DataFrame({"r": mr, "log_sd5": np.log(mr.rolling(5).std() + 1e-6), "log_sd20": np.log(mr.rolling(20).std() + 1e-6)})
    frames, ann_counts = {}, {}
    for c in codes:
        df = clean(c, start, calendar, log)
        if df is None:
            continue
        anns = announcements(c, years)
        ann_counts[c] = len(anns)
        frames[c] = (df, anns)
    cleaning = {"rules": [l.strip() for l in clean.__doc__.splitlines()[1:] if l.strip()], "counts": dict(log),
                "stocks_kept": len(frames), "stocks_requested": len(codes),
                "announcements": {"stocks_with_any": sum(1 for v in ann_counts.values() if v), "total": sum(ann_counts.values())}}
    print("[data]", json.dumps(cleaning["counts"]), "kept", len(frames), "ann", cleaning["announcements"], f"{time.time() - t0:.0f}s")
    if stage == "data":
        return {"cleaning": cleaning}
    first = (date.fromisoformat(start) + timedelta(days=500)).isoformat()  # ~1.4 years of history before testing
    panels = []
    for c, (df, anns) in frames.items():
        F = build_features(df, mkt, anns)
        F = F[F.r.notna() | (df.halted.reindex(F.index) == 0)]
        fi = int(np.searchsorted(F.index.values, first))
        g = garch_path(F["r"], fi, HORIZONS)
        for h in HORIZONS:
            F[f"garch{h}"] = g[h]
        F["code"] = c
        panels.append(F)
    P = pd.concat(panels).reset_index().rename(columns={"index": "date"})
    P = P[P.r.notna() & P.rv22.notna() & P.log_sd60.notna()].reset_index(drop=True)
    print("[features]", len(P), "rows", P.code.nunique(), "stocks", f"{time.time() - t0:.0f}s",
          "nan share", round(float(P[[f for f in FEATURES if f in P]].isna().mean().mean()), 3))
    if stage == "features":
        return {"cleaning": cleaning, "rows": len(P)}
    # size groups by 60-day median value traded at the time (top 50 by value traded that day = 大盘)
    P["value_rank"] = P.groupby("date")["log_value"].rank(ascending=False)
    P["size"] = np.where(P.value_rank <= 50, "大盘（成交额前 50）", "中小盘")
    results = {}
    latest_models = {}
    for h in HORIZONS:
        P[f"naive{h}"] = P["rv20"] * h
        hf, hp = har_fit(h)
        P[f"har{h}"] = walk_forward_pooled(P, hf, hp, h, first)
        P["log_garch_h"] = np.log(P[f"garch{h}"] + 1e-10)
        P["log_har_h"] = np.log(P[f"har{h}"] + 1e-10)
        feats = [f for f in FEATURES if f in P]
        lf, lp = lgb_fit(h, feats)
        P[f"lgb{h}"] = walk_forward_pooled(P[P[f"har{h}"].notna() | (P.date < first)].copy(), lf, lp, h, first).reindex(P.index)
        E = P[(P.date >= first) & P[f"y{h}"].notna() & P[f"garch{h}"].notna() & P[f"har{h}"].notna() & P[f"lgb{h}"].notna()]
        names = [("naive", "最笨基准：过去 20 日波动"), ("garch", "GARCH(1,1)"), ("har", "HAR-RV"), ("lgb", "LightGBM")]
        res = {"overall": {}, "groups": {}, "by_year": {}, "per_stock": {}}
        y = E[f"y{h}"].values
        for k, nm in names:
            res["overall"][nm] = metrics(y, E[f"{k}{h}"].values, h)
        for gname, mask in (("大盘（成交额前 50）", E["size"] == "大盘（成交额前 50）"), ("中小盘", E["size"] == "中小盘"),
                            ("公告期（未来窗口内有财报/停牌/配股/并购公告）", E[f"ev{h}"] > 0), ("平常期", E[f"ev{h}"] == 0)):
            G = E[mask]
            if len(G) > 100:
                res["groups"][gname] = {nm: metrics(G[f"y{h}"].values, G[f"{k}{h}"].values, h) for k, nm in names}
        for yr, G in E.groupby(E.date.str[:4]):
            res["by_year"][yr] = {nm: metrics(G[f"y{h}"].values, G[f"{k}{h}"].values, h)["qlike"] for k, nm in names}
        for c, G in E.groupby("code"):
            res["per_stock"][c] = {k: metrics(G[f"y{h}"].values, G[f"{k}{h}"].values, h) for k, _ in names}
        # keep a model only if it beats the previous kept one on QLIKE and RMSE
        kept, ladder = "naive", []
        for k, nm in names[1:]:
            a, b = res["overall"][nm], res["overall"][dict(names)[kept]]
            win = a["qlike"] < b["qlike"] and a["rmse"] < b["rmse"]
            ladder.append({"model": nm, "vs": dict(names)[kept], "beats": win, "qlike": a["qlike"], "vs_qlike": b["qlike"],
                           "rmse": a["rmse"], "vs_rmse": b["rmse"]})
            if win:
                kept = k
        res["ladder"], res["chosen"] = ladder, dict(names)[kept]
        res["beats_naive"] = {nm: (res["overall"][nm]["qlike"] < res["overall"][names[0][1]]["qlike"] and
                                   res["overall"][nm]["rmse"] < res["overall"][names[0][1]]["rmse"]) for _, nm in names[1:]}
        results[str(h)] = res
        latest_models[h] = (kept, feats, hf, hp, lf, lp)
        print(f"[models h={h}]", json.dumps(res["overall"], ensure_ascii=False), "chosen:", res["chosen"], f"{time.time() - t0:.0f}s")
    # latest forecasts with the chosen model, refit on everything known
    latest = []
    lastrows = P.groupby("code").tail(1)
    for h in HORIZONS:
        kept, feats, hf, hp, lf, lp = latest_models[h]
        tr = P[P[f"y{h}"].notna()]
        if kept == "har":
            P.loc[lastrows.index, f"now{h}"] = hp(hf(tr), lastrows)
        elif kept == "lgb":
            P["log_garch_h"] = np.log(P[f"garch{h}"] + 1e-10)
            P["log_har_h"] = np.log(P[f"har{h}"].fillna(P[f"naive{h}"]) + 1e-10)
            P.loc[lastrows.index, f"now{h}"] = lp(lf(tr[tr[f"har{h}"].notna()]), P.loc[lastrows.index])
        elif kept == "garch":
            P.loc[lastrows.index, f"now{h}"] = P.loc[lastrows.index, f"garch{h}"]
        else:
            P.loc[lastrows.index, f"now{h}"] = P.loc[lastrows.index, f"naive{h}"]
    for i, row in P.loc[lastrows.index].iterrows():
        c = row["code"]
        G = P[P.code == c]
        item = {"code": c, "date": row["date"], "size": row["size"]}
        for h in HORIZONS:
            f = float(row.get(f"now{h}", np.nan))
            if not np.isfinite(f):
                f = float(row[f"naive{h}"])
            v = float(to_vol(f, h))
            hist = to_vol(G[f"y{h}"].dropna().values, h)
            hist1y = to_vol(G[f"y{h}"].dropna().values[-252:], h)
            ps = results[str(h)]["per_stock"].get(c, {})
            chosen_key = latest_models[h][0]
            conf = "低"
            if ps and chosen_key != "naive":
                m_, n_ = ps[chosen_key], ps["naive"]
                if m_["qlike"] < n_["qlike"] * 0.95 and m_["rmse"] < n_["rmse"] and (m_["corr"] or 0) >= 0.5:
                    conf = "高"
                elif m_["qlike"] < n_["qlike"]:
                    conf = "中"
            elif ps:
                conf = "中" if (ps["naive"]["corr"] or 0) >= 0.5 else "低"
            item[f"vol{h}"] = round(v, 1)
            item[f"pct{h}"] = round(100 * float(np.mean(hist <= v)), 0) if len(hist) else None
            item[f"median1y{h}"] = round(float(np.median(hist1y)), 1) if len(hist1y) else None
            item[f"conf{h}"] = conf
            item[f"naive_vol{h}"] = round(float(to_vol(row[f"naive{h}"], h)), 1)
        item["alert"] = bool((item.get("pct20") or 0) >= 90 or (item.get("median1y20") and item["vol20"] >= 1.5 * item["median1y20"]))
        item["days_since_results"] = int(row["days_since_results"])
        item["after_reopen"] = bool(row["after_reopen5"] > 0)
        latest.append(item)
    latest.sort(key=lambda x: -(x.get("pct20") or 0))
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "data_from": start, "test_from": first,
           "stocks": int(P.code.nunique()), "rows": int(len(P)), "cleaning": cleaning, "results": results,
           "latest": latest, "alerts": [x for x in latest if x["alert"]],
           "api_calls": dict(_calls), "stats": dict(STATS), "minutes": round((time.time() - t0) / 60, 1),
           "notes": ["ASX 200 成分股名单没有免费来源，用“最近 60 日成交额前 N 名”近似。",
                     "换手率需要流通股数（EODHD 当前套餐没有），用成交额（价格×成交量）及其相对 20 日的变化代替。",
                     "下次财报日用已发生的财报周期估算（上次财报 + 约 182 天），不使用未来信息。"]}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--n", type=int, default=int(os.environ.get("VOL_N", "200")))
    ap.add_argument("--stage", default="all")
    ap.add_argument("--out", default=os.environ.get("STUDY61_OUT", str(study.DATA / "volml_results.json")))
    a = ap.parse_args()
    extra = [c.strip().upper() for c in (os.environ.get("VOL_EXTRA", "") + "," + Path("vol_watchlist.txt").read_text()
                                         if Path("vol_watchlist.txt").exists() else os.environ.get("VOL_EXTRA", "")).split(",") if c.strip()]
    codes = [c.strip().upper() for c in a.codes.split(",") if c.strip()] or universe(a.n, extra)
    out = run(codes, a.out, a.stage)
    print(json.dumps({k: out[k] for k in out if k in ("cleaning", "rows")}, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
