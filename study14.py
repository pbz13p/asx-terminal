"""Round 14: the fair comparison — dividends and cash interest included.

Price-index tests ignore two things: buy-and-hold collects ~4% a year in dividends (plus franking),
and a part-time strategy earns interest on idle cash. Here:
- index total return = ASX 200 accumulation index if EODHD has it, else price index + 4.0%/yr spread daily
- idle cash earns the RBA cash rate (F1 table, daily)
- grid of streak rules (2/3/4 down days x hold 3/5/10 days) to show whether the result is robust or a
  lucky setting; plus the 'fear' rule from round 5.
"""
import bisect
import json
import math
import os
from pathlib import Path

import study
import study7

COST = 0.001


def stats(daily):
    eq = peak = 1.0
    dd = 0.0
    for r in daily:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    m = sum(daily) / len(daily)
    sd = math.sqrt(sum((r - m) ** 2 for r in daily) / len(daily))
    return {"cagr": 100 * (eq ** (252 / len(daily)) - 1), "maxdd": 100 * dd, "sharpe": m / sd * math.sqrt(252) if sd else 0}


def main():
    tr_src = None
    for sym in ("AXJT.INDX", "XJT.INDX", "AXJOA.INDX"):
        study.download([sym])
        rows = study.load(sym)
        if len(rows) > 2000:
            tr_src = sym
            break
    study.download(["AXJO.INDX"])
    px = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    days = [d for d in sorted(px) if d >= "2014-06-01"]
    if tr_src:
        tr = {r[0]: r[4] for r in study.load(tr_src)}
        days = [d for d in days if d in tr]
        ret = [0.0] + [tr[days[i]] / tr[days[i - 1]] - 1 for i in range(1, len(days))]
    else:
        ret = [0.0] + [px[days[i]] / px[days[i - 1]] - 1 + 0.04 / 252 for i in range(1, len(days))]
    c = [px[d] for d in days]
    f1 = study7.rba_table("f1").get("FIRMMCRTD", {})
    ck = sorted(f1)

    def cash(d):
        j = bisect.bisect_right(ck, d) - 1
        return (f1[ck[j]] / 100 / 252) if j >= 0 else 0.0
    start = 202
    out = {"total_return_source": tr_src or "价格指数 + 4%/年股息估算", "rules": {}}
    bh = ret[start:]
    out["rules"]["一直持有(含股息)"] = {"all": stats(bh), "exposure": 100}
    cash_only = [cash(days[i]) for i in range(start, len(days))]
    out["rules"]["全部放现金"] = {"all": stats(cash_only), "exposure": 0}
    for need in (2, 3, 4):
        for hold in (3, 5, 10):
            pos, held, daily, n = 0, 0, [], 0
            for i in range(start, len(days)):
                daily.append(ret[i] if pos else cash(days[i]))
                if pos:
                    held += 1
                    if held >= hold:
                        pos = 0
                        daily[-1] -= COST
                else:
                    if all(c[i - k] < c[i - k - 1] for k in range(need)):
                        pos, held, n = 1, 0, n + 1
            dd = days[start:]
            sp = next(k for k, d in enumerate(dd) if d >= study.SPLIT)
            out["rules"][f"连跌{need}天·持{hold}天"] = {"all": stats(daily), "in": stats(daily[:sp]), "out": stats(daily[sp:]),
                                                     "trades": n}
    p = Path(os.environ.get("STUDY14_OUT", study.DATA / "study14_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
