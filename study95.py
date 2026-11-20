"""Round 95: does the 200-day line work as a market-state switch for our small-cap signals?

Frank 2026-11-21 asked why Claude keeps citing the 200-day line: is it really effective, simple or weighted?
Honest answer: it is the best-known trend line (Faber 2007 etc.), never tested on OUR small-cap signals. This round
tests it, with the rules fixed BEFORE any result is seen.

Pre-registered (2026-11-21):
  Regime: Small Ordinaries index (AXSO, fallback XSO; ASX 200 only if neither exists) close on the signal day vs its
          moving average on that same day (signal at the close, buy next open -> no look-ahead).
  Variants (6, all reported): SMA / EMA x 150 / 200 / 250 sessions. PRIMARY = SMA 200.
  Part A (event study): every S1/S2/S3 signal from round 94 (2015 on), buy next open, sell at the close after the
          rule's hold (S1 20 sessions, S2/S3 252), minus round 71 costs both sides. Split "index above line" vs
          "below". Reported for all signals and for the F1-passing ones (stock itself above its own 200-day SMA).
          Difference t: cluster = signal month.
  Part B (account): round 94's whole ledger (S1-S3 + F1/F2/X1, same account rules) with switch M = no NEW satellite
          buys while the index is below the line (holdings are not sold). Pre-tax and 32.5% tax; 2015-21 in sample,
          2022 on out of sample.
  Verdict "有效" only if ALL hold: primary SMA 200 improves the account out of sample (pre-tax CAGR up or max drawdown
          at least 3 points shallower with CAGR not lower); at least 5 of the 6 variants point the same way; Part A
          "above minus below" for the primary has t > 2 on all signals. Only one parameter working = coincidence = "无效".
"""
import json
import os
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import study
import study94 as L

VARIANTS = [(k, n) for k in ("SMA", "EMA") for n in (150, 200, 250)]
PRIMARY = ("SMA", 200)
HOLD = {"S1": 20, "S2": 252, "S3": 252}


def index_series():
    for sym in ("AXSO.INDX", "XSO.INDX", "AXJO.INDX"):
        try:
            study.download([sym])
        except Exception as e:
            print("[95] download", sym, type(e).__name__, flush=True)
        try:
            rows = study.load(sym)
        except Exception:
            rows = []
        if len(rows) > 1500:
            return sym, pd.Series({r[0]: float(r[4]) for r in rows}).sort_index()
    raise SystemExit("[95] no index series")


def regimes(close):
    out = {}
    for kind, n in VARIANTS:
        ma = close.rolling(n).mean() if kind == "SMA" else close.ewm(span=n, adjust=False, min_periods=n).mean()
        on = close >= ma
        out[f"{kind}{n}"] = {d: bool(v) for d, v, m in zip(close.index, on, ma) if m == m}
    return out


def build():
    """Same signal set and context as round 94 (copied so round 94 itself is untouched)."""
    days, acc, px, vix, f1, f2, rba = L.market()
    import bisect
    didx = {d: k for k, d in enumerate(days)}
    ck = sorted(f1)
    cash = (lambda d: f1[ck[bisect.bisect_right(ck, d) - 1]] / 100 / 252) if ck else (lambda d: 0.0)
    wplan = L.plan_weights(days, px, vix, f2, rba, L.PLAN_C)
    month_end = {days[k] for k in range(len(days) - 1) if days[k][:7] != days[k + 1][:7]}
    frames, ev_by_day, halts_by_day = {}, defaultdict(list), defaultdict(list)
    for k, fp in enumerate(sorted(study.CACHE.glob("*.AU.json"))):
        code = fp.name[:-8]
        try:
            s = L.signals_for(code, days, month_end)
        except Exception:
            continue
        if not s or (not s["events"] and not s["halts"]):
            continue
        keep = [e for e in s["events"] if e[0] >= "2014-12-01"]
        for (d, rule, turn, sg, above) in keep:
            ev_by_day[d].append((code, rule, turn, sg, above))
        for d in s["halts"]:
            halts_by_day[d].append(code)
        if keep:
            g = s["f"]
            frames[code] = L.Bars(list(g.index), g["o"].to_numpy(float), g["c"].to_numpy(float), g["raw"].to_numpy(float),
                                  g["traded"].to_numpy(bool))
        if k % 1000 == 0:
            print(f"[95] {k} stocks", flush=True)
    ctx = {"days": days, "acc": acc, "px": px, "wplan": wplan, "frames": frames, "ev_by_day": ev_by_day, "cash": cash,
           "halts_by_day": halts_by_day, "didx": didx, "del_by_day": L.index_deletions(),
           "sector": L.sectors(sorted(frames))}
    return ctx


def event_returns(ctx):
    days, frames = ctx["days"], ctx["frames"]
    out = []
    for d, evs in ctx["ev_by_day"].items():
        if d < L.START:
            continue
        for (code, rule, turn, sg, above) in evs:
            f = frames.get(code)
            k = f.pos.get(d) if f else None
            if k is None or k + 1 >= len(f.c):
                continue
            j = min(k + HOLD[rule], len(f.c) - 1)
            o, raw = f.o[k + 1], f.raw[k + 1]
            if not (o == o and o > 0 and f.traded[k + 1]) or not (f.c[j] == f.c[j]):
                continue
            r = f.c[j] / o - 1 - 2 * L.side_cost(raw)
            out.append({"d": d, "rule": rule, "r": float(np.clip(r, -1, 20)), "f1": bool(above)})
    return out


def compare(rows):
    """mean net return above vs below, t of the difference with month clusters."""
    def cl(xs):
        by = defaultdict(list)
        for x in xs:
            by[x["d"][:7]].append(x["r"])
        m = np.array([np.mean(v) for v in by.values()])
        return m, len(xs)
    a, na = cl([x for x in rows if x["on"]])
    b, nb = cl([x for x in rows if not x["on"]])
    res = {"n_above": na, "n_below": nb}
    if len(a) >= 3 and len(b) >= 3:
        res.update({"mean_above_pct": round(100 * a.mean(), 2), "mean_below_pct": round(100 * b.mean(), 2),
                    "hit_above_pct": round(100 * np.mean([x["r"] > 0 for x in rows if x["on"]]), 1),
                    "hit_below_pct": round(100 * np.mean([x["r"] > 0 for x in rows if not x["on"]]), 1)})
        se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        res["t_diff"] = round(float((a.mean() - b.mean()) / se), 2) if se > 0 else None
    return res


def main():
    t0 = time.time()
    sym, close = index_series()
    reg = regimes(close)
    ctx = build()
    ev = event_returns(ctx)
    out = {"round": 95, "built": time.strftime("%Y-%m-%d"), "index": sym, "rules": __doc__.split("Pre-registered")[1][:1800],
           "n_events": len(ev), "trials": len(VARIANTS)}
    # Part A
    A = {}
    for name, rg in reg.items():
        A[name] = {}
        for subset, keep in (("all", lambda x: True), ("f1_only", lambda x: x["f1"])):
            rows = [dict(x, on=rg.get(x["d"], True)) for x in ev if keep(x)]
            A[name][subset] = compare(rows)
            for rule in ("S1", "S2", "S3"):
                A[name][f"{subset}_{rule}"] = compare([x for x in rows if x["rule"] == rule])
        A[name]["pct_days_below"] = round(100 * np.mean([not v for d, v in rg.items() if d >= L.START]), 1)
    out["events"] = A
    print("[95] part A done", flush=True)   # numbers stay in the encrypted file
    # Part B
    full, sw = set(L.RULES), set(L.SWITCHES)
    B = {}
    for rate, tag in ((None, "pre_tax"), (0.325, "tax_325")):
        B[tag] = {"no_switch": L.summary(L.simulate(ctx, full, sw, rate))}
        for name, rg in reg.items():
            B[tag][name] = L.summary(L.simulate(dict(ctx, regime=rg), full, sw | {"M"}, rate))
        print(f"[95] part B {tag} done", flush=True)
    out["account"] = B
    # verdict
    base = B["pre_tax"]["no_switch"]["out_2022_on"]

    def better(s):
        o = s["out_2022_on"]
        return o["cagr"] > base["cagr"] or (o["maxdd"] - base["maxdd"] >= 3 and o["cagr"] >= base["cagr"])
    votes = {n: better(B["pre_tax"][n]) for n in reg}
    prim = f"{PRIMARY[0]}{PRIMARY[1]}"
    t_prim = A[prim]["all"].get("t_diff")
    ok = votes[prim] and sum(votes.values()) >= 5 and t_prim is not None and t_prim > 2
    out["verdict"] = {"primary": prim, "primary_improves_oos": votes[prim], "variants_improving": sum(votes.values()),
                      "variants": votes, "primary_event_t": t_prim, "label": "有效" if ok else "无效"}
    out["seconds"] = round(time.time() - t0)
    p = Path(os.environ.get("STUDY95_OUT", "out/study95_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[95] done", out["seconds"], "s", flush=True)


if __name__ == "__main__":
    main()
