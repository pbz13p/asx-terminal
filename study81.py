"""Round 81: is "pure news" priced too slowly in ASX small caps? (free local embeddings, no API cost)

Method from Didisheim, Kelly, Pourmohammadi & Tian, "The Inefficient Pricing of News" (NBER w35093, May 2026):
embed the text, remove the part that stock characteristics already predict, and test whether the remaining
"pure news" predicts returns. Here the text is the company's own price-sensitive ASX announcements (round 68 archive).

1. Embeddings: BAAI/bge-small-en-v1.5 (384 dims, ONNX via fastembed, GitHub Actions CPU). The WHOLE announcement is
   read: split at page markers into ~350-word chunks (about 512 tokens), every chunk embedded, the document vector is
   the normalised mean (at most 60 chunks; longer documents use 60 chunks spread evenly). Cached per announcement id
   in data/emb81 (encrypted cache + backup, never in the repo). Resumable: if the time budget runs out, the next run
   continues and the analysis waits until every document is embedded.
2. Monthly panel: each company at each month end (2016 on): characteristics (log turnover as size, log price, 1/6/12-month
   momentum, 60-day volatility, abnormal turnover), round-69 rule features (quarters of cash left, director on-market
   buys in the last 6 months, recent capital raise and its discount), and the mean announcement vector over the last
   1/3/6 months. Only announcements dated on or before the month end; entry at the next trading day's open.
3. PCA to 48 dims, fitted on pre-2023 documents only. "Pure news" = residual of each PCA dim after a monthly
   cross-sectional regression on the characteristics + rule features.
4. Walk-forward ridge (retrained every year, only data whose outcome was known by then) predicts 1/3/6/12-month excess
   return over the Small Ordinaries; long the top quintile. Models: A rules only, B rules + raw news, C rules + pure
   news. Event version: score each announcement on its own, buy at the next open, hold 3 months.
5. Costs: round 71 real cost by price level (0.2% brokerage + one price step); 3% round trip as a comparison. Stocks
   with 20-day median turnover under A$20k are excluded.
6. Topics: k-means (20 clusters, fitted on pre-2023 documents); per topic, the announcement-day reaction and what
   followed (continuation = under-reaction, reversal = over-reaction).
Validation: walk-forward 2019-2022, true out-of-sample 2023 on. Valid only if OOS net annualised Sharpe > 1 and monthly
t > 2; groups with fewer than 100 trades are marked untrustworthy. Also reports expectancy, hit rate, average win/loss,
biggest winner's share, longest losing streak and the 10-bagger hit rate (CLAUDE.md goal).
"""
import bisect
import gzip
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import study
import study69

ANN = study.DATA / "ann"
EMB = study.DATA / "emb81"
OUT = Path(os.environ.get("STUDY81_OUT", "out/study81_results.json"))
BUDGET = 60 * float(os.environ.get("EMB_MINUTES", "200"))
MODEL = "BAAI/bge-small-en-v1.5"
WORDS, MAX_CHUNKS, PCA_DIMS = 350, 60, 48
SPLIT = "2023-01"
H = (1, 3, 6, 12)


# ------------------------------------------------------------------ documents and embeddings
def load_docs():
    sens, rules = [], []
    companies = 0
    for f in sorted(ANN.glob("*.json.gz")):
        d = json.loads(gzip.decompress(f.read_bytes()))
        if not d.get("flag_v2"):
            continue
        companies += 1
        code = d["code"]
        for ids, doc in d["docs"].items():
            text = doc.get("text")
            if not text:
                continue
            date = doc["date"][:10]
            types = study69.classify(doc["headline"])
            e = {"code": code, "date": date}
            if "季度现金报告" in types:
                e.update(study69.cash_info(text))
            if "董事持股变动" in types:
                e.update(study69.director_trade(text))
            if "配股/融资" in types:
                p = study69.raise_price(text)
                if p:
                    e["raise_price"] = p
            if len(e) > 2:
                rules.append(e)
            if doc.get("sensitive"):
                sens.append({"id": ids, "code": code, "date": date, "headline": doc["headline"], "text": text})
    sens.sort(key=lambda x: (x["code"], x["date"]))
    return sens, rules, companies


def chunks(text):
    words = re.sub(r"\[page \d+\]", " ", text).split()
    parts = [" ".join(words[i:i + WORDS]) for i in range(0, len(words), WORDS)] or [""]
    if len(parts) > MAX_CHUNKS:
        idx = np.linspace(0, len(parts) - 1, MAX_CHUNKS).round().astype(int)
        parts = [parts[i] for i in idx]
    return parts


def embed_all(docs, t0):
    """Returns {id: vector} for every embedded doc and how many are still missing."""
    EMB.mkdir(parents=True, exist_ok=True)
    have = {}
    for f in EMB.glob("*.npz"):
        z = np.load(f, allow_pickle=False)
        for i, v in zip(z["ids"], z["vec"]):
            have[str(i)] = v.astype(np.float32)
    todo = [d for d in docs if d["id"] not in have]
    print(f"[emb] have {len(have)}, to embed {len(todo)}", flush=True)
    if todo:
        from fastembed import TextEmbedding
        model = TextEmbedding(MODEL, threads=os.cpu_count())
        by_code = defaultdict(list)
        for d in todo:
            by_code[d["code"]].append(d)
        done = 0
        for code, ds in by_code.items():
            if time.time() - t0 > BUDGET:
                break
            texts, owner = [], []
            for k, d in enumerate(ds):
                for c in chunks(d["text"]):
                    texts.append("passage: " + c if "e5" in MODEL else c)
                    owner.append(k)
            vecs = np.array(list(model.embed(texts, batch_size=64)), dtype=np.float32)
            owner = np.array(owner)
            new_ids, new_vec = [], []
            for k, d in enumerate(ds):
                v = vecs[owner == k].mean(0)
                v /= np.linalg.norm(v) + 1e-9
                have[d["id"]] = v
                new_ids.append(d["id"])
                new_vec.append(v)
            f = EMB / f"{code}.npz"
            if f.exists():
                z = np.load(f)
                new_ids = list(z["ids"]) + new_ids
                new_vec = list(z["vec"].astype(np.float32)) + new_vec
            np.savez_compressed(f, ids=np.array(new_ids, dtype="U32"), vec=np.array(new_vec, dtype=np.float16))
            done += len(ds)
            if done % 2000 < len(ds):
                print(f"[emb] {done}/{len(todo)} docs, {time.time() - t0:.0f}s", flush=True)
    left = sum(d["id"] not in have for d in docs)
    return have, left


# ------------------------------------------------------------------ prices and panel
def tick(p):
    return 0.001 if p < 0.10 else 0.005 if p < 2 else 0.01


def prices(codes, cal):
    out = {}
    for code in codes:
        rows = study.load(f"{code}.AU")
        if len(rows) < 260:
            continue
        by = {r[0]: r for r in rows}
        last_day = rows[-1][0]
        a = np.full((len(cal), 4), np.nan)        # open (adj), close (adj), raw close, volume
        last = None
        for j, d in enumerate(cal):
            if d in by:
                r = by[d]
                a[j] = (r[1], r[4], r[6], r[5])
                last = r
            elif last is not None and d <= last_day:
                a[j] = (last[4], last[4], last[6], 0)   # no trade that day: carry the close, zero volume
        out[code] = a
    return out


def month_ends(cal, start="2016-01"):
    ends = {}
    for j, d in enumerate(cal):
        if d[:7] >= start:
            ends[d[:7]] = j
    return sorted(ends.items())


def build_panel(px, cal, bench, docs, vecs, rules, pca):
    ends = month_ends(cal)
    by_code_docs = defaultdict(list)
    for d in docs:
        if d["id"] in vecs:
            by_code_docs[d["code"]].append((d["date"], pca[d["id"]]))
    by_code_rules = defaultdict(list)
    for e in rules:
        by_code_rules[e["code"]].append(e)
    for v in by_code_rules.values():
        v.sort(key=lambda e: e["date"])
    rows = []
    bc = bench[:, 1]
    for code, a in px.items():
        o, c, raw, vol = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
        dl = by_code_docs.get(code, [])
        ddates = [x[0] for x in dl]
        rl = by_code_rules.get(code, [])
        rdates = [e["date"] for e in rl]
        for ym, i in ends:
            if i < 260 or i + 1 >= len(cal) or not np.isfinite(c[i]) or c[i] <= 0 or not np.isfinite(o[i + 1]):
                continue
            val = raw[i - 19:i + 1] * vol[i - 19:i + 1]
            if not np.all(np.isfinite(val)) or np.median(val) < 20000:
                continue
            d_end = cal[i]
            k = bisect.bisect_right(ddates, d_end)
            news = {}
            for w, days in ((1, 31), (3, 92), (6, 183)):
                lo = (pd.Timestamp(d_end) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
                j0 = bisect.bisect_right(ddates, lo)
                news[w] = (np.mean([x[1] for x in dl[j0:k]], 0) if k > j0 else None)
            if news[6] is None:
                continue                                   # needs at least one price-sensitive announcement in 6 months
            lr = np.diff(np.log(c[i - 60:i + 1]))
            v60 = raw[i - 59:i + 1] * vol[i - 59:i + 1]
            # round 69 features as of the month end
            kr = bisect.bisect_right(rdates, d_end)
            past = rl[:kr]
            six = (pd.Timestamp(d_end) - pd.Timedelta(days=183)).strftime("%Y-%m-%d")
            q = [e for e in past if e.get("quarters") is not None and e["date"] >= (pd.Timestamp(d_end) - pd.Timedelta(days=130)).strftime("%Y-%m-%d")]
            buys = [e.get("value") or 0 for e in past if e["date"] >= six and e.get("on_market") and not e.get("sold")]
            raises = [e for e in past if e["date"] >= six and e.get("raise_price")]
            disc = None
            if raises:
                j = bisect.bisect_left(cal, raises[-1]["date"])
                if 0 < j < len(cal) and np.isfinite(raw[j - 1]) and raw[j - 1] > 0:
                    disc = raises[-1]["raise_price"] / raw[j - 1] - 1
            r = {"code": code, "ym": ym, "i": i, "raw": raw[i], "cost": 0.002 + tick(raw[i]) / raw[i],
                 "log_value": math.log(np.nanmean(v60) + 1), "log_price": math.log(raw[i]),
                 "mom1": c[i] / c[i - 21] - 1, "mom6": c[i - 21] / c[i - 126] - 1, "mom12": c[i - 21] / c[i - 252] - 1,
                 "vol60": float(np.nanstd(lr)), "abturn": math.log((np.nanmean(val) + 1) / (np.nanmean(v60) + 1)),
                 "cash_q": min(q[-1]["quarters"], 12) if q else np.nan, "cash_known": float(bool(q)),
                 "dir_buy": math.log1p(sum(buys)), "raised": float(bool(raises)), "raise_disc": disc if disc is not None else 0.0,
                 "n_news": k - bisect.bisect_right(ddates, six)}
            for w in (1, 3, 6):
                r[f"news{w}"] = news[w] if news[w] is not None else news[6] * 0
            entry = o[i + 1]
            for h in H:
                j = min(i + 21 * h, len(cal) - 1)
                if i + 21 * h >= len(cal):
                    r[f"ex{h}"] = np.nan
                    continue
                seg = c[i + 1:j + 1]
                seg = seg[np.isfinite(seg)]
                exit_ = seg[-1] if len(seg) else entry           # delisted: last traded price
                r[f"ex{h}"] = exit_ / entry - 1 - (bc[j] / bench[i + 1, 0] - 1)
                if h == 12:
                    r["peak12"] = (np.nanmax(seg) / entry) if len(seg) else 1.0
            rows.append(r)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ modelling
CHARS = ["log_value", "log_price", "mom1", "mom6", "mom12", "vol60", "abturn"]
RULES = ["cash_q", "cash_known", "dir_buy", "raised", "raise_disc"]


def residualise(P, col):
    """Pure news: per month, remove the part of each news dim explained by characteristics + rule features."""
    X = P[CHARS + RULES].fillna(0).values
    N = np.stack(P[col].values)
    R = np.zeros_like(N)
    for ym, idx in P.groupby("ym").indices.items():
        x = np.column_stack([np.ones(len(idx)), X[idx]])
        x = (x - x.mean(0)) / (x.std(0) + 1e-9)
        x[:, 0] = 1
        beta, *_ = np.linalg.lstsq(x, N[idx], rcond=None)
        R[idx] = N[idx] - x @ beta
    return R


def zscore_by_month(P, M):
    Z = np.zeros_like(M, dtype=float)
    for ym, idx in P.groupby("ym").indices.items():
        m = M[idx]
        Z[idx] = (m - np.nanmean(m, 0)) / (np.nanstd(m, 0) + 1e-9)
    return np.nan_to_num(Z)


def walk_forward(P, X, h):
    from sklearn.linear_model import Ridge
    y = P[f"ex{h}"].values
    pred = np.full(len(P), np.nan)
    ym = P.ym.values
    for year in range(2019, int(P.ym.max()[:4]) + 1):
        start = f"{year}-01"
        cut = (pd.Timestamp(start + "-01") - pd.DateOffset(months=h + 1)).strftime("%Y-%m")   # outcome known by then
        tr = (ym <= cut) & np.isfinite(y)
        te = (ym >= start) & (ym <= f"{year}-12")
        if tr.sum() < 500 or not te.any():
            continue
        yt = np.clip(y[tr], *np.nanpercentile(y[tr], [1, 99]))
        months = sorted(set(ym[tr]))
        val = np.isin(ym, months[-24:]) & tr
        fit = tr & ~val
        best, best_ic = 100.0, -9
        for a in (1.0, 10.0, 100.0, 1000.0):     # alpha chosen on the last 24 training months only
            if fit.sum() < 300:
                break
            m = Ridge(alpha=a).fit(X[fit], np.clip(y[fit], *np.nanpercentile(y[fit], [1, 99])))
            ic = pd.Series(m.predict(X[val])).corr(pd.Series(y[val]), method="spearman")
            if ic > best_ic:
                best, best_ic = a, ic
        m = Ridge(alpha=best).fit(X[tr], yt)
        pred[te] = m.predict(X[te])
    return pred


def tstat_nw(x, lag):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 6:
        return None
    e = x - x.mean()
    v = (e @ e) / n
    for l in range(1, lag + 1):
        v += 2 * (1 - l / (lag + 1)) * (e[l:] @ e[:-l]) / n
    return float(x.mean() / math.sqrt(v / n)) if v > 0 else None


def evaluate(P, pred, h, label):
    T = P.assign(pred=pred)
    T = T[T.pred.notna() & T[f"ex{h}"].notna()]
    T = T.assign(q=T.groupby("ym").pred.rank(pct=True))
    out = {"model": label, "horizon_months": h}
    base10 = float((P.peak12 >= 10).mean()) if "peak12" in P else None
    for name, sel in (("2019-2022", T.ym < SPLIT), ("2023+ (OOS)", T.ym >= SPLIT)):
        G = T[sel]
        top = G[G.q > .8]
        if len(top) == 0:
            continue
        net = top[f"ex{h}"] - top.cost
        net3 = top[f"ex{h}"] - 0.03
        pm = net.groupby(top.ym).mean()
        q_means = [round(100 * float(G[(G.q > lo) & (G.q <= lo + .2)][f"ex{h}"].mean()), 2) for lo in (0, .2, .4, .6, .8)]
        wins, losses = net[net > 0], net[net <= 0]
        streak = cur = 0
        for v in pm.values:
            cur = cur + 1 if v <= 0 else 0
            streak = max(streak, cur)
        ann = pm.mean() * 12 / h
        sd = pm.std() * math.sqrt(12 / h) if len(pm) > 2 else float("nan")
        r = {"trades": int(len(top)), "months": int(len(pm)), "trustworthy": bool(len(top) >= 100),
             "quintiles_gross_pct": q_means, "top_gross_pct": round(100 * float(top[f"ex{h}"].mean()), 2),
             "top_net_pct": round(100 * float(net.mean()), 2), "top_net_3pct_cost": round(100 * float(net3.mean()), 2),
             "ann_net_pct": round(100 * float(ann), 1), "sharpe_net": round(float(ann / sd), 2) if sd and sd > 0 else None,
             "t_nw": round(tstat_nw(pm.values, max(0, h - 1)) or 0, 2),
             "expectancy_pct": round(100 * float(net.mean()), 2), "hit_rate": round(float((net > 0).mean()), 3),
             "avg_win_pct": round(100 * float(wins.mean()), 1) if len(wins) else None,
             "avg_loss_pct": round(100 * float(losses.mean()), 1) if len(losses) else None,
             "biggest_winner_share": round(float(wins.max() / wins.sum()), 3) if len(wins) and wins.sum() > 0 else None,
             "longest_losing_months": int(streak)}
        if "peak12" in top and base10:
            r["tenbagger_rate"] = round(float((top.peak12 >= 10).mean()), 4)
            r["tenbagger_base"] = round(base10, 4)
        out[name] = r
    return out


def topics(docs, vecs, px, cal, bench, k=20):
    from sklearn.cluster import KMeans
    train = [d for d in docs if d["id"] in vecs and d["date"] < SPLIT]
    if len(train) < 2000:
        return []
    X = np.stack([vecs[d["id"]] for d in train])
    km = KMeans(n_clusters=k, n_init=4, random_state=7).fit(X)
    rows = []
    allv = [d for d in docs if d["id"] in vecs]
    lab = km.predict(np.stack([vecs[d["id"]] for d in allv]))
    bc = bench[:, 1]
    for d, l in zip(allv, lab):
        a = px.get(d["code"])
        if a is None:
            continue
        j = bisect.bisect_left(cal, d["date"])
        if j < 1 or j + 251 >= len(cal):
            continue
        c = a[:, 1]
        if not (np.isfinite(c[j - 1]) and np.isfinite(c[j + 1])) or c[j - 1] <= 0:
            continue
        rx = c[j + 1] / c[j - 1] - 1 - (bc[j + 1] / bc[j - 1] - 1)              # 2-day announcement reaction
        post = {}
        for hd in (63, 250):
            seg = c[j + 1:j + 1 + hd]
            seg = seg[np.isfinite(seg)]
            post[hd] = seg[-1] / c[j + 1] - 1 - (bc[j + hd] / bc[j + 1] - 1) if len(seg) else np.nan
        rows.append({"topic": int(l), "rx": rx, "p63": post[63], "p250": post[250], "headline": d["headline"]})
    T = pd.DataFrame(rows)
    out = []
    stop = set("the of and to in for a on at with from by an quarterly report update announcement asx limited ltd company".split())
    for t, G in T.groupby("topic"):
        words = Counter(w for h in G.headline for w in re.findall(r"[a-z]{3,}", h.lower()) if w not in stop)
        s = np.sign(G.rx)
        cont63 = (s * G.p63).mean()
        n = len(G)
        se = (s * G.p63).std() / math.sqrt(n) if n > 2 else np.nan
        out.append({"topic": int(t), "n": int(n), "words": [w for w, _ in words.most_common(6)],
                    "reaction_pct": round(100 * float(G.rx.mean()), 2), "after63_pct": round(100 * float(G.p63.mean()), 2),
                    "after250_pct": round(100 * float(G.p250.mean()), 2),
                    "continuation63_pct": round(100 * float(cont63), 2), "t": round(float(cont63 / se), 2) if se and se > 0 else None,
                    "verdict": "反应不足（继续走）" if cont63 > 0 and se and cont63 / se > 2 else
                               "反应过度（反转）" if cont63 < 0 and se and cont63 / se < -2 else "不明显",
                    "trustworthy": bool(n >= 100)})
    return sorted(out, key=lambda r: -(r["continuation63_pct"] or 0))


def event_version(docs, vecs, pca, P, px, cal, bench):
    """Score each announcement on its own (pure news vs the month-end characteristics before it), buy next open, hold 63 days."""
    from sklearn.linear_model import Ridge
    if P.empty:
        return {}
    key = {(r.code, r.ym): k for k, r in enumerate(P[["code", "ym"]].itertuples(index=False))}
    X = P[CHARS + RULES].fillna(0).values
    bc = bench[:, 1]
    ev = []
    for d in docs:
        if d["id"] not in vecs:
            continue
        prev = (pd.Timestamp(d["date"][:7] + "-01") - pd.DateOffset(months=1)).strftime("%Y-%m")
        k = key.get((d["code"], prev))
        a = px.get(d["code"])
        if k is None or a is None:
            continue
        j = bisect.bisect_right(cal, d["date"])           # next trading day after the announcement date
        if j + 63 >= len(cal) or not np.isfinite(a[j, 0]) or a[j, 0] <= 0:
            continue
        seg = a[j:j + 63, 1]
        seg = seg[np.isfinite(seg)]
        ex = seg[-1] / a[j, 0] - 1 - (bc[j + 62] / bench[j, 0] - 1) if len(seg) else np.nan
        ev.append({"date": d["date"], "row": k, "vec": pca[d["id"]], "ex": ex, "cost": P.cost.values[k]})
    E = pd.DataFrame(ev)
    if len(E) < 1000:
        return {"events": int(len(E)), "note": "事件太少"}
    N = np.stack(E.vec.values)
    C = X[E.row.values]
    Cz = (C - C.mean(0)) / (C.std(0) + 1e-9)
    tr = (E.date < SPLIT).values & np.isfinite(E.ex.values)
    beta, *_ = np.linalg.lstsq(np.column_stack([np.ones(tr.sum()), Cz[tr]]), N[tr], rcond=None)   # fitted on training only
    pure = N - np.column_stack([np.ones(len(E)), Cz]) @ beta
    Xf = np.column_stack([Cz, pure])
    m = Ridge(alpha=100.0).fit(Xf[tr], np.clip(E.ex.values[tr], -1, 3))
    E = E.assign(pred=m.predict(Xf))
    te = E[E.date >= SPLIT].copy()
    te["q"] = te.pred.rank(pct=True)
    top = te[te.q > .8]
    net = top.ex - top.cost
    return {"events_train": int(tr.sum()), "events_oos": int(len(te)),
            "oos_quintiles_gross_pct": [round(100 * float(te[(te.q > lo) & (te.q <= lo + .2)].ex.mean()), 2) for lo in (0, .2, .4, .6, .8)],
            "oos_top_net_pct": round(100 * float(net.mean()), 2), "oos_top_trades": int(len(top)),
            "oos_hit_rate": round(float((net > 0).mean()), 3), "trustworthy": bool(len(top) >= 100)}


def main():
    t0 = time.time()
    docs, rules, companies = load_docs()
    print(f"[docs] {len(docs)} price-sensitive with text, {companies} companies (flag_v2), {len(rules)} rule events", flush=True)
    shard = os.environ.get("EMB_SHARD")  # "k/n": embed only this lane's companies (parallel lanes in emb81.yml)
    if shard:
        import zlib
        k, n = map(int, shard.split("/"))
        mine = lambda c: zlib.crc32(c.encode()) % n == k
        docs = [d for d in docs if mine(d["code"])]
        for f in EMB.glob("*.npz"):  # keep only this lane's files so lanes never overwrite each other with stale copies
            if not mine(f.stem):
                f.unlink()
        vecs, left = embed_all(docs, t0)
        print(f"[lane {shard}] docs {len(docs)}, embedded {len(vecs)}, left {left}", flush=True)
        Path("left.txt").write_text(str(left))
        return
    vecs, left = embed_all(docs, t0)
    out = {"method": __doc__.strip(), "companies": companies, "docs": len(docs), "embedded": len(vecs), "left_to_embed": left,
           "model": MODEL}
    if left > 0.02 * len(docs):   # new announcements keep arriving; under 2% missing does not change the result
        out["status"] = f"向量还没算完：还剩 {left} 篇，下次运行接着算"
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[stop]", out["status"])
        return
    cal = [r[0] for r in study.load("AXJO.INDX")]
    b = {r[0]: r for r in study.load("AXSO.INDX")}
    bench = np.array([(b[d][1], b[d][4]) if d in b else (np.nan, np.nan) for d in cal])
    bench = pd.DataFrame(bench).ffill().values
    px = prices({d["code"] for d in docs}, cal)
    from sklearn.decomposition import PCA
    train_ids = [d["id"] for d in docs if d["date"] < SPLIT and d["id"] in vecs]
    pca_m = PCA(n_components=PCA_DIMS, random_state=7).fit(np.stack([vecs[i] for i in train_ids]))
    ids = list(vecs)
    proj = pca_m.transform(np.stack([vecs[i] for i in ids]))
    pca = dict(zip(ids, proj))
    P = build_panel(px, cal, bench, docs, vecs, rules, pca)
    print(f"[panel] {len(P)} company-months, {P.code.nunique()} companies, {P.ym.min()}..{P.ym.max()}  {time.time() - t0:.0f}s", flush=True)
    base = zscore_by_month(P, P[CHARS + RULES].fillna(0).values)
    raw6 = zscore_by_month(P, np.stack(P.news6.values))
    pure = {w: zscore_by_month(P, residualise(P, f"news{w}")) for w in (1, 3, 6)}
    models = []
    for h in H:
        for label, X in (("A 只用规则特征（第69轮+量价）", base), ("B 规则 + 原始新闻向量", np.hstack([base, raw6])),
                         ("C 规则 + 纯新闻（6 个月）", np.hstack([base, pure[6]])), ("C1 规则 + 纯新闻（1 个月）", np.hstack([base, pure[1]])),
                         ("C3 规则 + 纯新闻（3 个月）", np.hstack([base, pure[3]])), ("D 只用纯新闻（6 个月）", pure[6])):
            if h in (3, 12) and label[0] in "C" and label[1] in "13":
                continue
            m = evaluate(P, walk_forward(P, X, h), h, label)
            models.append(m)
            print("[model]", m.get("model"), "h", m.get("horizon_months"), "done", flush=True)   # numbers stay in the encrypted file (public repo)
    out.update({"panel_rows": int(len(P)), "panel_companies": int(P.code.nunique()), "first": P.ym.min(), "last": P.ym.max(),
                "models": models, "event_version": event_version(docs, vecs, pca, P, px, cal, bench),
                "topics": topics(docs, vecs, px, cal, bench), "seconds": round(time.time() - t0)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("[done]", out["seconds"], "s")


if __name__ == "__main__":
    main()
