"""Round 78: deep learning on A-shares, compared with round 77's LightGBM on the same data, costs and tests.

Models (walk-forward, retrained every year on data up to the previous November, predicting the next year):
  1. LGBM   — round 77's best (34 textbook indicators + 476 formula alphas), the baseline.
  2. MLP    — neural network on the same features, each feature ranked across stocks every month (what most
              Chinese quant teams start from); 3 seeds averaged.
  3. GRU    — recurrent network reading the raw last 60 days of open/high/low/close/VWAP/volume (Qlib Alpha360 style):
              no hand-made factors at all, the network finds its own patterns.
  4. Ensemble — average rank of the three.
Target: next-20-day excess return ranked across stocks (-0.5..0.5). Outcome, costs, limit rules, big/small groups,
style-neutral alpha and stress episodes exactly as round 77. Deep learning only counts if it beats LGBM after
costs out of sample (2019 on) and in the style-neutral test.
"""
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import study71 as s71
import study77 as s77

OUT = Path(os.environ.get("STUDY78_OUT", "out/study78_results.json"))
SEQ = 60


def rank_norm(P, X):
    """Every feature ranked across stocks on each date, centred to -0.5..0.5; missing -> 0."""
    Z = np.zeros_like(X, dtype="float32")
    for d, idx in P.groupby("date").indices.items():
        Z[idx] = pd.DataFrame(X[idx]).rank(pct=True).fillna(0.5).values - 0.5
    return Z


def walk(P, fit_predict, label):
    pred = np.full(len(P), np.nan)
    years = sorted(P.date.str[:4].unique())
    for y in years:
        tr = (P.date < f"{int(y) - 1}-12-01").values
        if P.date[tr].nunique() < 36:
            continue
        te = (P.date.str[:4] == y).values
        t0 = time.time()
        pred[te] = fit_predict(tr, te)
        print(f"[{label}] {y} trained on {tr.sum()} rows, {time.time() - t0:.0f}s", flush=True)
    return pred


def torch_mlp(Z, y, seeds=3, epochs=4):
    import torch
    torch.set_num_threads(4)

    def fit_predict(tr, te):
        outs = []
        for seed in range(seeds):
            torch.manual_seed(seed)
            net = torch.nn.Sequential(torch.nn.Linear(Z.shape[1], 256), torch.nn.ReLU(), torch.nn.Dropout(0.2),
                                      torch.nn.Linear(256, 64), torch.nn.ReLU(), torch.nn.Dropout(0.2), torch.nn.Linear(64, 1))
            opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
            Xt, yt = torch.from_numpy(Z[tr]), torch.from_numpy(y[tr])
            n = len(Xt)
            for _ in range(epochs):
                perm = torch.randperm(n)
                net.train()
                for i in range(0, n, 4096):
                    b = perm[i:i + 4096]
                    opt.zero_grad()
                    loss = torch.nn.functional.mse_loss(net(Xt[b]).squeeze(1), yt[b])
                    loss.backward()
                    opt.step()
            net.eval()
            with torch.no_grad():
                outs.append(net(torch.from_numpy(Z[te])).squeeze(1).numpy())
        return np.mean(outs, axis=0)
    return fit_predict


def torch_gru(S, y, epochs=3, seeds=2):
    import torch
    torch.set_num_threads(4)

    class Net(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = torch.nn.GRU(S.shape[2], 32, batch_first=True)
            self.head = torch.nn.Linear(32, 1)

        def forward(self, x):
            _, h = self.gru(x)
            return self.head(h[-1]).squeeze(1)

    def fit_predict(tr, te):
        outs = []
        for seed in range(seeds):
            torch.manual_seed(seed)
            net = Net()
            opt = torch.optim.Adam(net.parameters(), lr=1e-3)
            idx = np.where(tr)[0]
            rng = np.random.default_rng(seed)
            for _ in range(epochs):
                rng.shuffle(idx)
                net.train()
                for i in range(0, len(idx), 2048):
                    b = idx[i:i + 2048]
                    opt.zero_grad()
                    loss = torch.nn.functional.mse_loss(net(torch.from_numpy(S[b])), torch.from_numpy(y[b]))
                    loss.backward()
                    opt.step()
            net.eval()
            te_idx = np.where(te)[0]
            p = np.zeros(len(te_idx), dtype="float32")
            with torch.no_grad():
                for i in range(0, len(te_idx), 8192):
                    p[i:i + 8192] = net(torch.from_numpy(S[te_idx[i:i + 8192]])).numpy()
            outs.append(p)
        return np.mean(outs, axis=0)
    return fit_predict


def main():
    d = s77.prepare(judge_factors=False, seq_len=SEQ)
    P, X, cat, done, S, t0 = d["P"], d["X"], d["cat"], d["done"], d["S"], d["t0"]
    base_feats = s71.NUM + ["ret5", "log_price", "log_value"]
    P["log_price"], P["log_value"] = np.log(P.raw), np.log(P.value60 + 1)
    XF = np.hstack([P[base_feats].values.astype("float32"), X[:, done]])
    names = base_feats + [cat[k]["id"] for k in done]
    y = (P.groupby("date").ex20.rank(pct=True).values - 0.5).astype("float32")
    models, preds = [], {}

    lg = s77.lgb_eval(P, XF, names, "LGBM (round 77 best)")
    preds["lgbm"] = lg.pop("_pred")
    models.append(lg)
    print("[lgbm]", json.dumps({k: lg[k] for k in ("big", "small")}, ensure_ascii=False)[:500], f"{time.time() - t0:.0f}s", flush=True)

    Z = rank_norm(P, XF)
    preds["mlp"] = walk(P, torch_mlp(Z, y), "mlp")
    models.append(s77.evaluate(P, preds["mlp"], "MLP neural net on the same features", Z.shape[1]))
    print("[mlp]", json.dumps({k: models[-1][k] for k in ("big", "small")}, ensure_ascii=False)[:500], f"{time.time() - t0:.0f}s", flush=True)
    del Z

    preds["gru"] = walk(P, torch_gru(S, y), "gru")
    models.append(s77.evaluate(P, preds["gru"], "GRU on raw 60-day prices (no factors)", SEQ * S.shape[2]))
    print("[gru]", json.dumps({k: models[-1][k] for k in ("big", "small")}, ensure_ascii=False)[:500], f"{time.time() - t0:.0f}s", flush=True)

    R = pd.DataFrame({k: v for k, v in preds.items()}).assign(date=P.date.values)
    ens = R.groupby("date")[list(preds)].rank(pct=True).mean(axis=1).values
    models.append(s77.evaluate(P, ens, "Ensemble: average rank of LGBM + MLP + GRU", None))
    print("[ensemble]", json.dumps({k: models[-1][k] for k in ("big", "small")}, ensure_ascii=False)[:500], flush=True)

    out = {"method": __doc__.strip(), "rows": int(len(P)), "dates": int(P.date.nunique()), "first": P.date.min(), "last": P.date.max(),
           "models": models, "seconds": round(time.time() - t0)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("[done]", out["seconds"], "s")


if __name__ == "__main__":
    main()
