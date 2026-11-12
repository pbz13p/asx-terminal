"""Round 46: pre-announcement drift. ASX 200 return on the session BEFORE RBA decisions, on RBA day
(2:30pm announcement inside the session), and on the ASX session that ends just before an FOMC decision
(same calendar date in Sydney time). Compared with ordinary days; both periods."""
import json
import math
import os
from pathlib import Path

import study
import study4


def main():
    rows = [r for r in study.load("AXJO.INDX") if r[0] >= "2014-12-01"]
    ds = [r[0] for r in rows]
    ret = {ds[i]: rows[i][4] / rows[i - 1][4] - 1 for i in range(1, len(ds))}
    allr = [v for d, v in ret.items() if d >= "2015-01-01"]
    base = sum(allr) / len(allr)
    sd = math.sqrt(sum((x - base) ** 2 for x in allr) / len(allr))
    ev = study4.fallback_events()
    idx = {d: i for i, d in enumerate(ds)}
    groups = {"澳联储决议前一天": [], "澳联储决议当天": [], "美联储决议当天(澳股收盘时尚未公布)": [], "美联储决议前一天": []}
    for d in ev["澳联储 利率决议"]:
        if d in idx and idx[d] > 1 and d >= "2015-01-01":
            groups["澳联储决议当天"].append(d)
            groups["澳联储决议前一天"].append(ds[idx[d] - 1])
    for d in ev["美联储 利率决议"]:
        if d in idx and idx[d] > 1 and d >= "2015-01-01":
            groups["美联储决议当天(澳股收盘时尚未公布)"].append(d)
            groups["美联储决议前一天"].append(ds[idx[d] - 1])
    out = {"base": round(100 * base, 3)}
    for k, dl in groups.items():
        xs = [ret[d] for d in dl if d in ret]
        m = sum(xs) / len(xs)
        a = [ret[d] for d in dl if d in ret and d < study.SPLIT]
        b = [ret[d] for d in dl if d in ret and d >= study.SPLIT]
        out[k] = {"n": len(xs), "mean": round(100 * m, 3), "up": round(100 * sum(1 for x in xs if x > 0) / len(xs)),
                  "t": round((m - base) / (sd / math.sqrt(len(xs))), 2),
                  "2015–21": round(100 * sum(a) / len(a), 3) if a else None, "2022后": round(100 * sum(b) / len(b), 3) if b else None}
    p = Path(os.environ.get("STUDY46_OUT", study.DATA / "study46_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
