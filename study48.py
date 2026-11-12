"""Round 48: month-of-year seasonality redone on the ASX 200 ACCUMULATION index (dividends included),
which removes the ex-dividend bias of round 9 (March and September looked weak partly because banks
and miners go ex-dividend then). 2001+ if available, split 2015-21 / 2022+ as well."""
import json
import os
from collections import defaultdict
from pathlib import Path

import study


def main():
    rows = study.load("AXJOA.INDX")
    me = {}
    for r in rows:
        me[r[0][:7]] = r[4]
    keys = sorted(me)
    by = defaultdict(lambda: defaultdict(list))
    for a, b in zip(keys, keys[1:]):
        r = me[b] / me[a] - 1
        m = int(b[5:])
        by[m]["all"].append((b[:4], r))
        by[m]["2015–21" if "2015" <= b < "2022" else "2022后" if b >= "2022" else "2014前"].append((b[:4], r))
    out = {"first": keys[0], "last": keys[-1]}
    for m in range(1, 13):
        g = by[m]
        xs = [x for _, x in g["all"]]
        out[m] = {"n": len(xs), "mean": round(100 * sum(xs) / len(xs), 2), "up": round(100 * sum(1 for x in xs if x > 0) / len(xs)),
                  **{p: round(100 * sum(x for _, x in g[p]) / len(g[p]), 2) for p in ("2015–21", "2022后") if g[p]}}
    p = Path(os.environ.get("STUDY48_OUT", study.DATA / "study48_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
