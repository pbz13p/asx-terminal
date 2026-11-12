"""Round 30: ASX calendar mechanics.
  - SPI 200 futures / index options quarterly expiry: 3rd Thursday of Mar, Jun, Sep, Dec
    (index rebalance takes effect after the 3rd Friday of those months)
  - Monthly options expiry (3rd Thursday of other months)
  - Day before a weekday ASX holiday (pre-holiday effect)
  - Financial-year turn: last 3 sessions of June, first 3 of July
ASX 200 price index; average day return vs all days; t-stats; both periods.
"""
import json
import math
import os
from datetime import date, timedelta
from pathlib import Path

import study


def third(y, m, wd):
    d = date(y, m, 1)
    while d.weekday() != wd:
        d += timedelta(days=1)
    return d + timedelta(days=14)


def main():
    study.download(["AXJO.INDX", "AXSO.INDX"])
    out = {}
    for name, sym in (("ASX 200", "AXJO.INDX"), ("小盘", "AXSO.INDX")):
        rows = [r for r in study.load(sym) if r[0] >= "2014-06-01"]
        ds = [r[0] for r in rows]
        c = [r[4] for r in rows]
        ret = {ds[i]: c[i] / c[i - 1] - 1 for i in range(1, len(ds))}
        allr = [v for d, v in ret.items() if d >= "2015-01-01"]
        base = sum(allr) / len(allr)
        sd_all = math.sqrt(sum((x - base) ** 2 for x in allr) / len(allr))
        sets = {"季度交割日(3月/6/9/12月第3个周四)": [], "季度交割后一日(调整生效前的周五)": [], "季度交割周(周一到周五)": [],
                "月度期权到期日(其他月份第3个周四)": [], "公众假期前一天": [], "财年末最后3天(6月)": [], "新财年前3天(7月)": []}
        dset = set(ds)
        for y in range(2015, 2027):
            for m in range(1, 13):
                t = third(y, m, 3).isoformat()
                if t not in dset:
                    continue
                if m in (3, 6, 9, 12):
                    sets["季度交割日(3月/6/9/12月第3个周四)"].append(t)
                    f = (date.fromisoformat(t) + timedelta(days=1)).isoformat()
                    if f in dset:
                        sets["季度交割后一日(调整生效前的周五)"].append(f)
                    mon = date.fromisoformat(t) - timedelta(days=3)
                    for k in range(5):
                        x = (mon + timedelta(days=k)).isoformat()
                        if x in dset:
                            sets["季度交割周(周一到周五)"].append(x)
                else:
                    sets["月度期权到期日(其他月份第3个周四)"].append(t)
            june = [d for d in ds if d.startswith(f"{y}-06")]
            july = [d for d in ds if d.startswith(f"{y}-07")]
            sets["财年末最后3天(6月)"] += june[-3:]
            sets["新财年前3天(7月)"] += july[:3]
        for i in range(1, len(ds) - 1):
            nd = date.fromisoformat(ds[i + 1])
            dd = date.fromisoformat(ds[i])
            gap = [dd + timedelta(days=k) for k in range(1, (nd - dd).days)]
            if any(g.weekday() < 5 for g in gap) and ds[i] >= "2015-01-01":
                sets["公众假期前一天"].append(ds[i])
        res = {"_base_day": 100 * base}
        for k, dl in sets.items():
            xs = [ret[d] for d in dl if d in ret and d >= "2015-01-01"]
            if not xs:
                continue
            m = sum(xs) / len(xs)
            ins = [ret[d] for d in dl if d in ret and "2015" <= d < study.SPLIT]
            oos = [ret[d] for d in dl if d in ret and d >= study.SPLIT]
            res[k] = {"n": len(xs), "mean": 100 * m, "up": 100 * sum(1 for x in xs if x > 0) / len(xs),
                      "t_vs_normal": (m - base) / (sd_all / math.sqrt(len(xs))),
                      "in": 100 * sum(ins) / len(ins) if ins else None, "out": 100 * sum(oos) / len(oos) if oos else None}
        out[name] = res
    p = Path(os.environ.get("STUDY30_OUT", study.DATA / "study30_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
