"""Round 20: China as a driver of ASX materials (and the ASX 200).

Proxies: FXI (China large caps, US-listed), USDCNY (yuan), VALE (iron ore producer ADR, iron-ore proxy).
Questions (weekly samples since 2015, both periods):
  - after China stocks move > +/-8% in 20 days, do ASX materials beat the ASX 200 over the next 20 days?
  - yuan strengthening / weakening over 3 months
  - VALE 20-day trend as an iron-ore proxy
  - overnight: FXI / VALE US-session move vs the next ASX open and intraday for BHP, RIO, FMG
"""
import bisect
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import study


def ser(sym):
    return {r[0]: r for r in study.load(sym)}


def main():
    syms = ["FXI.US", "VALE.US", "USDCNY.FOREX", "AXMJ.INDX", "AXJO.INDX", "BHP.AU", "RIO.AU", "FMG.AU"]
    study.download(syms)
    fxi, vale, cny = ({d: r[4] for d, r in ser(s).items()} for s in ("FXI.US", "VALE.US", "USDCNY.FOREX"))
    mj, xj = ({d: r[4] for d, r in ser(s).items()} for s in ("AXMJ.INDX", "AXJO.INDX"))
    keys = {k: sorted(v) for k, v in (("fxi", fxi), ("vale", vale), ("cny", cny))}
    src = {"fxi": fxi, "vale": vale, "cny": cny}

    def before(name, d, back=0):
        ks = keys[name]
        j = bisect.bisect_left(ks, d) - 1 - back
        return src[name][ks[j]] if j >= 0 else None
    days = [d for d in sorted(mj) if d in xj and d >= "2014-06-01"]
    groups = defaultdict(lambda: defaultdict(list))
    for i in range(70, len(days) - 20, 5):
        d = days[i]
        if d < "2015-01-01":
            continue
        per = "2015–21" if d < study.SPLIT else "2022后"
        ex = (mj[days[i + 20]] / mj[d] - 1) - (xj[days[i + 20]] / xj[d] - 1)
        mk = xj[days[i + 20]] / xj[d] - 1
        tags = []
        a, b = before("fxi", d), before("fxi", d, 20)
        if a and b:
            c = a / b - 1
            tags.append("中国股 20 天涨>8%" if c > .08 else "中国股 20 天跌>8%" if c < -.08 else "中国股平稳")
        a, b = before("cny", d), before("cny", d, 63)
        if a and b:
            c = a / b - 1
            tags.append("人民币 3 个月升值>2%" if c < -.02 else "人民币 3 个月贬值>2%" if c > .02 else "人民币平稳")
        a, b = before("vale", d), before("vale", d, 20)
        if a and b:
            c = a / b - 1
            tags.append("VALE 20 天涨>10%" if c > .10 else "VALE 20 天跌>10%" if c < -.10 else "VALE 平稳")
        for t in tags:
            for p in ("all", per):
                groups[t][p].append((ex, mk))
    reg = {t: {p: {"n": len(v), "materials_ex": 100 * sum(x[0] for x in v) / len(v),
                   "xjo": 100 * sum(x[1] for x in v) / len(v)} for p, v in ps.items()} for t, ps in groups.items()}
    # overnight
    ov = {}
    for us_sym, us in (("FXI", fxi), ("VALE", vale)):
        uk = sorted(us)
        for stock in ("BHP", "RIO", "FMG"):
            s = ser(f"{stock}.AU")
            sd = sorted(s)
            big = defaultdict(list)
            for i in range(1, len(sd)):
                d, p = sd[i], sd[i - 1]
                if d < "2015-01-01":
                    continue
                j = bisect.bisect_left(uk, d) - 1
                if j < 1 or uk[j] <= p:  # the US session must fall between the two ASX sessions
                    continue
                ur = us[uk[j]] / us[uk[j - 1]] - 1
                o, c, pc = s[d][1], s[d][4], s[p][4]
                if o <= 0 or pc <= 0:
                    continue
                k = "美股时段涨>3%" if ur > .03 else "美股时段跌>3%" if ur < -.03 else None
                if k:
                    big[k].append((o / pc - 1, c / o - 1))
            ov[f"{us_sym}→{stock}"] = {k: {"n": len(v), "gap": 100 * sum(x[0] for x in v) / len(v),
                                          "intraday": 100 * sum(x[1] for x in v) / len(v)} for k, v in big.items()}
    p = Path(os.environ.get("STUDY20_OUT", study.DATA / "study20_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"regimes": reg, "overnight": ov}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"regimes": reg, "overnight": ov}, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
