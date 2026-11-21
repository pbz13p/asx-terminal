"""Round 96: what the AI's careful reading of small-cap announcements (round 76, ~57k scored) is worth in price terms.

Frank 2026-11-21: "只做小盘股……在小盘股里认真读原始公告，找出回报的空间。" Round 76 finished reading every
price-sensitive announcement in the archive (anonymised, whole text, one JSON score each, cached in data/ai/s76/).
This round is the event study that round 76 promised. Free: no AI calls, only the cached scores and prices.

Pre-registered 2026-11-21, before any result was seen:
  Events: every scored announcement dated 2022-01-01 on. Entry = adjusted open of the first session AFTER the
    announcement date (announcements can come during the day; no look-ahead). Tradable only: entry-day raw close >= 1c
    and 20-session median traded value before the announcement >= A$20k.
  Returns: to the adjusted close 20 / 60 / 250 sessions after entry, minus round 71 costs (0.2% + one tick per side),
    minus the Small Ordinaries (AXSO) return over the same days -> "excess". Right tail: share that at any close within
    250 sessions reached 2x the entry price.
  Groups (each compared with all other scored announcements; t of the difference with month clusters):
    G1 good surprise   importance >= 4 and vs_prior = above
    G2 bad surprise    importance >= 4 and vs_prior = below
    G3 hype            hype = 3
    G4 dilution        dilution_risk = 3
    G5 short of cash   cash_quarters <= 1 (stated)
    G6 raising         type = capital_raising
    G7 clean good news importance >= 4, above, hype <= 1, dilution_risk <= 1
  K-line check (云聪 / Wittgenstein: news vs price): within G1 and G7, entry moved to the 2nd session's open; "confirmed"
    = the 1st session after the announcement closes >= 5% above the close before it; compare confirmed vs not.
  Periods: all, 2022-2024, 2025 on, and after the model's knowledge cutoff (2026-07-01 on, 20-session horizon only).
  Bars (CLAUDE.md): |t| >= 3 on the 2025-on period AND same sign in 2022-2024 = 有效; |t| 2-3 = 一般; else 无效.
  Groups with fewer than 100 events in a period are marked 不可信. 7 groups x 3 horizons + 2 checks = 23 tests.
"""
import bisect
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

import study
import study94 as L

AI = study.DATA / "ai" / "s76"
H = (20, 60, 250)
START, SPLIT, CUTOFF = "2022-01-01", "2025-01-01", "2026-07-01"
GROUPS = {
    "G1": lambda s: (s.get("importance") or 0) >= 4 and s.get("vs_prior") == "above",
    "G2": lambda s: (s.get("importance") or 0) >= 4 and s.get("vs_prior") == "below",
    "G3": lambda s: (s.get("hype") or 0) >= 3,
    "G4": lambda s: (s.get("dilution_risk") or 0) >= 3,
    "G5": lambda s: isinstance(s.get("cash_quarters"), (int, float)) and s["cash_quarters"] <= 1,
    "G6": lambda s: s.get("type") == "capital_raising",
    "G7": lambda s: (s.get("importance") or 0) >= 4 and s.get("vs_prior") == "above" and (s.get("hype") or 0) <= 1
    and (s.get("dilution_risk") or 0) <= 1,
}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    t0 = time.time()
    scores = []
    for f in AI.glob("*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s.get("date", "") >= START and s.get("code"):
            for k in ("importance", "hype", "dilution_risk", "cash_quarters"):
                s[k] = num(s.get(k))
            scores.append(s)
    print("[96] scored announcements", len(scores), flush=True)
    try:
        study.download(["AXSO.INDX"])
    except Exception:
        pass
    ix = study.load("AXSO.INDX")
    ixd = [r[0] for r in ix]
    ixc = [r[4] for r in ix]

    def ix_ret(d0, d1):
        i, j = bisect.bisect_left(ixd, d0), bisect.bisect_left(ixd, d1)
        if i >= len(ixc) or j >= len(ixc) or i == 0:
            return None
        return ixc[j] / ixc[i - 1] - 1          # from the close before entry day to the exit close

    by_code = defaultdict(list)
    for s in scores:
        by_code[s["code"]].append(s)
    ev = []
    for code, lst in by_code.items():
        rows = study.load(f"{code}.AU")
        if len(rows) < 30:
            continue
        ds = [r[0] for r in rows]
        val = [r[6] * r[5] for r in rows]          # raw close x volume
        for s in lst:
            i = bisect.bisect_right(ds, s["date"])  # first session after the announcement date
            if i < 21 or i >= len(rows):
                continue
            raw = rows[i][6]
            if raw < 0.01 or statistics.median(val[i - 20:i]) < 20_000:
                continue
            e = {"d": s["date"], "g": [g for g, f in GROUPS.items() if f(s)]}
            o = rows[i][1]
            cost = 2 * L.side_cost(raw)
            for h in H:
                j = i + h
                if j < len(rows):
                    ir = ix_ret(rows[i][0], rows[j][0])
                    if ir is not None:
                        e[h] = rows[j][4] / o - 1 - cost - ir
            hi = max(r[4] for r in rows[i:i + 251]) if i + 1 < len(rows) else o
            e["x2"] = hi >= 2 * o if i + 250 < len(rows) else None
            # K-line check: entry at the 2nd session's open; confirmed = 1st session close >= +5% on the prior close
            if i + 1 < len(rows):
                e["conf"] = rows[i][4] >= 1.05 * rows[i - 1][4]
                o2, raw2 = rows[i + 1][1], rows[i + 1][6]
                for h in (20, 60):
                    j = i + 1 + h
                    if j < len(rows) and raw2 > 0:
                        ir = ix_ret(rows[i + 1][0], rows[j][0])
                        if ir is not None:
                            e[f"k{h}"] = rows[j][4] / o2 - 1 - 2 * L.side_cost(raw2) - ir
            ev.append(e)
    print("[96] tradable events", len(ev), flush=True)

    def cl(rows, key):
        by = defaultdict(list)
        for x in rows:
            if x.get(key) is not None:
                by[x["d"][:7]].append(float(np.clip(x[key], -1, 10)))
        return np.array([np.mean(v) for v in by.values()]), sum(len(v) for v in by.values()), [y for v in by.values() for y in v]

    def diff(a_rows, b_rows, key):
        a, na, av = cl(a_rows, key)
        b, nb, bv = cl(b_rows, key)
        r = {"n": na, "n_rest": nb}
        if na == 0 or nb == 0:
            return r
        r.update({"mean_pct": round(100 * float(np.mean(av)), 2), "median_pct": round(100 * float(np.median(av)), 2),
                  "hit_pct": round(100 * float(np.mean([v > 0 for v in av])), 1),
                  "rest_mean_pct": round(100 * float(np.mean(bv)), 2)})
        if len(a) >= 3 and len(b) >= 3:
            se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
            r["t"] = round(float((a.mean() - b.mean()) / se), 2) if se > 0 else None
        r["trust"] = "不可信" if na < 100 else "ok"
        return r

    periods = {"all": lambda d: True, "2022_2024": lambda d: d < SPLIT, "2025_on": lambda d: d >= SPLIT,
               "after_cutoff": lambda d: d >= CUTOFF}
    out = {"round": 96, "built": time.strftime("%Y-%m-%d"), "rules": __doc__.split("Pre-registered")[1][:2600],
           "scored": len(scores), "tradable_events": len(ev), "groups": {}}
    base_x2 = [x["x2"] for x in ev if x.get("x2") is not None]
    out["base_doubled_pct"] = round(100 * float(np.mean(base_x2)), 1) if base_x2 else None
    for g in GROUPS:
        res = {}
        for pn, pf in periods.items():
            P = [x for x in ev if pf(x["d"])]
            A_ = [x for x in P if g in x["g"]]
            B_ = [x for x in P if g not in x["g"]]
            res[pn] = {f"h{h}": diff(A_, B_, h) for h in (H if pn != "after_cutoff" else (20,))}
            x2 = [x["x2"] for x in A_ if x.get("x2") is not None]
            res[pn]["doubled_pct"] = round(100 * float(np.mean(x2)), 1) if x2 else None
        # verdict per horizon
        verd = {}
        for h in H:
            t_new = res["2025_on"][f"h{h}"].get("t")
            t_old = res["2022_2024"][f"h{h}"].get("t")
            if t_new is None or t_old is None or res["2025_on"][f"h{h}"].get("trust") != "ok":
                verd[h] = "不可信"
            elif abs(t_new) >= 3 and np.sign(t_new) == np.sign(t_old):
                verd[h] = "有效"
            elif abs(t_new) >= 2 and np.sign(t_new) == np.sign(t_old):
                verd[h] = "一般"
            else:
                verd[h] = "无效"
        res["verdict"] = verd
        out["groups"][g] = res
    # K-line check
    kc = {}
    for g in ("G1", "G7"):
        G = [x for x in ev if g in x["g"] and "conf" in x]
        kc[g] = {pn: {f"h{h}": diff([x for x in G if x["conf"] and pf(x["d"])], [x for x in G if not x["conf"] and pf(x["d"])], f"k{h}")
                      for h in (20, 60)} for pn, pf in periods.items() if pn != "after_cutoff"}
    out["kline_check"] = kc
    out["seconds"] = round(time.time() - t0)
    p = Path(os.environ.get("STUDY96_OUT", "out/study96_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, default=float), encoding="utf-8")
    print("[96] done", out["seconds"], "s", flush=True)   # numbers stay in the encrypted file


if __name__ == "__main__":
    main()
