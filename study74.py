"""Round 74 (daily): A-share model picks for the terminal, using the round 72 model.

1. Bring data/cn up to date with EODHD's whole-exchange file for the last trading day (2 calls).
2. Train LightGBM on the round 72 panel (every 5th day since 2010, 20-day excess return, main board only).
3. Score every main-board stock on the latest day; "big" group = top 1000 by 60-day value traded.
   top = best 10% (skipping stocks that closed limit-up, which can't be bought), avoid = worst 10%.
Output: out/cn_picks.json (date, top, avoid) -> encrypted on the cn-results branch.
"""
import gzip
import json
import os
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

import study71 as s71
import study72 as s72

CN = Path("data/cn")
KEY = os.environ.get("EODHD_API_KEY", "")


def update_last_day():
    added = 0
    for ex in ("SHG", "SHE"):
        try:
            with urllib.request.urlopen(f"https://eodhd.com/api/eod-bulk-last-day/{ex}?api_token={KEY}&fmt=json", timeout=120) as r:
                rows = json.loads(r.read())
        except Exception as e:
            print("[bulk failed]", ex, type(e).__name__)
            continue
        for r in rows:
            f = CN / f"{r['code']}.{ex}.json.gz"
            if not f.exists() or not r.get("close"):
                continue
            data = json.loads(gzip.decompress(f.read_bytes()))
            if data and data[-1][0] >= r["date"]:
                continue
            data.append([r["date"], r.get("open"), r.get("high"), r.get("low"), r["close"], r.get("adjusted_close") or r["close"], r.get("volume")])
            f.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode()))
            added += 1
    print("[update] rows added", added)


def cn_names():
    try:
        req = urllib.request.Request("http://www.cninfo.com.cn/new/data/szse_stock.json", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            return {s["code"]: s.get("zwjc") for s in json.loads(r.read()).get("stockList", [])}
    except Exception:
        return {}


def latest_features():
    rows = []
    files = [f for f in sorted(CN.glob("*.json.gz")) if f.name.startswith(s72.MAIN)]
    last_day = None
    for f in files:
        df = s72.load(f)
        if df is None or len(df) < 260:
            continue
        df = df.iloc[-400:]
        c, h, l, v, raw = df.c, df.h.fillna(df.c), df.l.fillna(df.c), df.v, df.raw
        F = s71.indicators(c, h, l, v)
        F["raw"], F["close"] = raw, c
        F["value60"] = (raw * v).rolling(60, min_periods=40).median()
        F["ret5"] = c / c.shift(5) - 1
        r1 = c / c.shift() - 1
        x = F.iloc[-1].copy()
        x["date"] = df.index[-1]
        x["code"] = f.name[:6]
        x["limit_up"] = r1.iloc[-1] >= 0.097
        rows.append(x)
        last_day = max(last_day or "", df.index[-1])
    L = pd.DataFrame(rows)
    L = L[L.date == last_day]  # traded on the latest day (suspended stocks drop out)
    return L.replace([np.inf, -np.inf], np.nan), last_day


def main():
    t0 = time.time()
    if KEY:
        update_last_day()
    P = s72.build()
    feats = s71.NUM + ["ret5", "log_price", "log_value"]
    P["log_price"], P["log_value"] = np.log(P.raw), np.log(P.value60 + 1)
    import lightgbm as lgb
    params = dict(objective="regression", learning_rate=0.03, num_leaves=31, min_data_in_leaf=1000, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7)
    mdl = lgb.train(params, lgb.Dataset(P[feats], P.ex20.clip(-.5, 1)), num_boost_round=300)
    print("[model]", len(P), "rows", f"{time.time() - t0:.0f}s")
    L, day = latest_features()
    L["log_price"], L["log_value"] = np.log(L.raw.astype(float)), np.log(L.value60.astype(float) + 1)
    L = L[L.value60.notna()].copy()
    L["rk"] = L.value60.rank(ascending=False)
    B = L[L.rk <= 1000].copy()
    B["pred"] = mdl.predict(B[feats].astype(float))
    B["q"] = B.pred.rank(pct=True)
    names = cn_names()

    def rec(r):
        return {"code": r.code, "name": names.get(r.code, ""), "price": round(float(r.raw), 2), "score": round(100 * float(r.q)),
                "ret20": round(100 * float(r.roc20), 1) if pd.notna(r.roc20) else None,
                "vol20": round(100 * float(r.vol20)) if pd.notna(r.vol20) else None}
    top = B[(B.q > .9) & ~B.limit_up.astype(bool)].sort_values("q", ascending=False)
    avoid = B[B.q <= .1].sort_values("q")
    out = {"date": day, "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "universe": int(len(B)),
           "top": [rec(r) for r in top.itertuples()], "avoid": [rec(r) for r in avoid.itertuples()],
           "note": "第 72 轮模型：成交额前 1000 的主板股票，模型分前 10% / 后 10%，持有约 20 个交易日。", "seconds": round(time.time() - t0)}
    path = os.environ.get("STUDY74_OUT", "out/cn_picks.json")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(out, ensure_ascii=False))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
