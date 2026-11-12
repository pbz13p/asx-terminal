"""Round 45: is 'market starts pricing cuts' (AU 2y yield falls) knife-edge?
Grid: lookback 42/63/126 sessions x threshold 0.3/0.5/0.7%; forward 20/60-session ASX 200 accumulation return
on signal weeks minus the average of all weeks; independent episodes count; both periods."""
import bisect
import json
import os
from pathlib import Path

import study
import study7


def main():
    acc = {r[0]: r[4] for r in study.load("AXJOA.INDX")}
    days = [d for d in sorted(acc) if d >= "2014-06-01"]
    f2 = study7.rba_table("f2")["FCMYGBAG2D"]
    yk = sorted(f2)
    base = {}
    for h in (20, 60):
        xs = [acc[days[i + h]] / acc[days[i]] - 1 for i in range(0, len(days) - h, 5) if days[i] >= "2015-01-01"]
        base[h] = sum(xs) / len(xs)
    out = {}
    for lb in (42, 63, 126):
        for thr in (.3, .5, .7):
            sig = {"all": {20: [], 60: []}, "2015–21": {20: [], 60: []}, "2022后": {20: [], 60: []}}
            eps, last = 0, -999
            for i in range(0, len(days) - 60, 5):
                d = days[i]
                if d < "2015-01-01":
                    continue
                j = bisect.bisect_right(yk, d) - 1
                if j < lb or f2[yk[j]] - f2[yk[j - lb]] >= -thr:
                    continue
                if i - last > 60:
                    eps += 1
                last = i
                per = "2015–21" if d < study.SPLIT else "2022后"
                for h in (20, 60):
                    r = acc[days[i + h]] / acc[d] - 1 - base[h]
                    sig["all"][h].append(r)
                    sig[per][h].append(r)
            out[f"{lb // 21}个月·跌>{thr}%"] = {"episodes": eps, **{p: {str(h): (len(v), round(100 * sum(v) / len(v), 2) if v else None) for h, v in hs.items()} for p, hs in sig.items()}}
    p = Path(os.environ.get("STUDY45_OUT", study.DATA / "study45_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"base": base, "grid": out}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
