"""Round 41: market breadth (share of liquid stocks above their 200-day average) as an index timing signal.

Weekly, liquid universe (20-day median turnover >= $1M). Buckets: <20% (washed out), 20-40, 40-60, 60-80, >80%.
Forward ASX 200 accumulation returns over 20 / 60 sessions, independent episodes for the extremes, both periods.
Also the 20-day change in breadth (thrust): a jump of +25 points from a low base.
"""
import json
import os
from collections import defaultdict
from pathlib import Path

import study


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    days = [d for d in sorted(acc) if d >= "2014-06-01"]
    fridays = days[::5]
    above = defaultdict(lambda: [0, 0])
    for f in study.CACHE.glob("*.AU.json"):
        rows = study.load(f.name[:-5])
        if len(rows) < 260:
            continue
        idx = {r[0]: k for k, r in enumerate(rows)}
        cl = [r[4] for r in rows]
        pre = [0.0]
        for x in cl:
            pre.append(pre[-1] + x)
        for d in fridays:
            i = idx.get(d)
            if i is None or i < 200:
                continue
            turn = sorted(rows[k][6] * rows[k][5] for k in range(i - 19, i + 1))[10]
            if turn < 1e6:
                continue
            ma = (pre[i + 1] - pre[i - 199]) / 200
            above[d][0] += cl[i] > ma
            above[d][1] += 1
    br = {d: 100 * a / n for d, (a, n) in above.items() if n >= 100}
    keys = sorted(br)
    pos = {d: i for i, d in enumerate(days)}
    out = defaultdict(lambda: defaultdict(list))
    eps = defaultdict(list)
    for j, d in enumerate(keys):
        i = pos[d]
        if d < "2015-01-01" or i + 60 >= len(days):
            continue
        b = br[d]
        bucket = "<20%" if b < 20 else "20–40%" if b < 40 else "40–60%" if b < 60 else "60–80%" if b < 80 else ">80%"
        f20 = acc[days[i + 20]] / acc[d] - 1
        f60 = acc[days[i + 60]] / acc[d] - 1
        per = "2015–21" if d < study.SPLIT else "2022后"
        for k in (bucket, f"{bucket}|{per}"):
            out[k]["f20"].append(f20)
            out[k]["f60"].append(f60)
        if j >= 4:
            thrust = b - br[keys[j - 4]]
            if thrust >= 25 and br[keys[j - 4]] < 40:
                if not eps["breadth_thrust"] or (pos[d] - pos[eps["breadth_thrust"][-1][0]]) > 60:
                    eps["breadth_thrust"].append((d, round(100 * f20, 2), round(100 * f60, 2)))
        if bucket == "<20%":
            if not eps["washout"] or (pos[d] - pos[eps["washout"][-1][0]]) > 60:
                eps["washout"].append((d, round(100 * f20, 2), round(100 * f60, 2)))
    mean = lambda a: round(100 * sum(a) / len(a), 2) if a else None
    res = {"buckets": {k: {"n": len(v["f20"]), "f20": mean(v["f20"]), "f60": mean(v["f60"]),
                           "up60": round(100 * sum(1 for x in v["f60"] if x > 0) / len(v["f60"])) if v["f60"] else None}
                       for k, v in sorted(out.items())},
           "episodes": eps, "now": {"date": keys[-1], "breadth": round(br[keys[-1]], 1),
                                    "4w_ago": round(br[keys[-5]], 1) if len(keys) > 5 else None}}
    p = Path(os.environ.get("STUDY41_OUT", study.DATA / "study41_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
