"""Round 47: (a) the hawkish-RBA week for small caps and sectors; (b) ASX 200 drawdowns: how deep, how long
to recover (accumulation index), and what came next after a -10% drawdown."""
import json
import os
from pathlib import Path

import study
import study4
import study7
from study9 import SECTORS


def main():
    f2 = study7.rba_table("f2")["FCMYGBAG2D"]
    yk = sorted(f2)
    rba = sorted(d for d in study4.fallback_events()["澳联储 利率决议"] if d in f2 and d >= "2015-01-01")
    idx_syms = {"ASX 200": "AXJO.INDX", **SECTORS}
    out = {"hawk_week": {}}
    for name, sym in idx_syms.items():
        s = {r[0]: r[4] for r in study.load(sym)}
        ds = sorted(s)
        pos = {d: i for i, d in enumerate(ds)}
        hw, rest = [], []
        for d in rba:
            k = yk.index(d)
            if d not in pos or k == 0 or pos[d] + 5 >= len(ds):
                continue
            w = s[ds[pos[d] + 5]] / s[d] - 1
            (hw if f2[d] - f2[yk[k - 1]] >= .06 else rest).append(w)
        if hw and rest:
            out["hawk_week"][name] = {"hawk_n": len(hw), "hawk": round(100 * sum(hw) / len(hw), 2), "rest": round(100 * sum(rest) / len(rest), 2)}
    acc = [(r[0], r[4]) for r in study.load("AXJOA.INDX") if r[0] >= "2014-01-01"]
    peak, peak_d, dd_events, in_dd = acc[0][1], acc[0][0], [], None
    for i, (d, v) in enumerate(acc):
        if v >= peak:
            if in_dd:
                in_dd["recovered"] = d
                in_dd["days_to_recover"] = i - in_dd["i_peak"]
                dd_events.append(in_dd)
                in_dd = None
            peak, peak_d, ip = v, d, i
        else:
            dd = v / peak - 1
            if dd <= -.10 and in_dd is None:
                in_dd = {"peak": peak_d, "i_peak": ip, "hit10": d, "i10": i, "trough": dd, "trough_d": d}
            if in_dd and dd < in_dd["trough"]:
                in_dd["trough"], in_dd["trough_d"] = dd, d
    if in_dd:
        dd_events.append(in_dd)
    for e in dd_events:
        i10 = e["i10"]
        e["after_hit10_60d"] = round(100 * (acc[min(len(acc) - 1, i10 + 60)][1] / acc[i10][1] - 1), 1)
        e["after_hit10_250d"] = round(100 * (acc[min(len(acc) - 1, i10 + 250)][1] / acc[i10][1] - 1), 1)
        e["trough"] = round(100 * e["trough"], 1)
        for k in ("i_peak", "i10"):
            e.pop(k, None)
    out["drawdowns_10pct"] = dd_events
    p = Path(os.environ.get("STUDY47_OUT", study.DATA / "study47_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
