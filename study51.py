"""Round 51: cleaner RBA surprise = AU 2y move on decision day minus the US 2y move of the previous US session
(strips out global bond moves). Compare next-week ASX 200 after 'hawkish' (top third) vs the rest, overall,
2015-21, 2022-23 and 2024+ (new two-day meeting format)."""
import bisect
import json
import os
from pathlib import Path

import study
import study4
import study7


def main():
    f2 = study7.rba_table("f2")["FCMYGBAG2D"]
    yk = sorted(f2)
    us2 = study7.fred("DGS2")
    uk = sorted(us2)
    px = {r[0]: r[4] for r in study.load("AXJO.INDX")}
    ds = sorted(px)
    pos = {d: i for i, d in enumerate(ds)}
    rows = []
    for d in sorted(study4.fallback_events()["澳联储 利率决议"]):
        if d not in f2 or d not in pos or d < "2015-01-01" or pos[d] + 5 >= len(ds):
            continue
        k = yk.index(d)
        j = bisect.bisect_left(uk, d) - 1
        if k == 0 or j < 1:
            continue
        au = f2[d] - f2[yk[k - 1]]
        us = us2[uk[j]] - us2[uk[j - 1]]
        rows.append((d, au, au - us, px[ds[pos[d] + 5]] / px[d] - 1))
    out = {}
    for name, col in (("原始(澳2年变动)", 1), ("扣除美债(澳2年-美2年)", 2)):
        srt = sorted(rows, key=lambda r: r[col])
        cut = srt[2 * len(srt) // 3][col]
        res = {"cut_bp": round(100 * cut, 1)}
        for per, sel in (("全部", lambda d: True), ("2015–21", lambda d: d < "2022"), ("2022–23", lambda d: "2022" <= d < "2024"), ("2024后", lambda d: d >= "2024")):
            hw = [r[3] for r in rows if sel(r[0]) and r[col] >= cut]
            rs = [r[3] for r in rows if sel(r[0]) and r[col] < cut]
            res[per] = {"hawk_n": len(hw), "hawk": round(100 * sum(hw) / len(hw), 2) if hw else None,
                        "rest_n": len(rs), "rest": round(100 * sum(rs) / len(rs), 2) if rs else None}
        out[name] = res
    p = Path(os.environ.get("STUDY51_OUT", study.DATA / "study51_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
