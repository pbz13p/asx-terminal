"""Daily announcement lists from the AI scores (round 96), run right after round 76 reads the day's new announcements.

Frank 2026-11-21: put round 96 into the terminal as its own section.
  不碰名单 (avoid): big dilution (dilution_risk 3), capital raising, or heavy hype (hype 3) -- round 96: 20 sessions
      after these, small caps did 3-4 points worse than after other announcements.
  干净好消息 (clean good news): importance >= 4, better than the company's previous one, hype <= 1, dilution <= 1 --
      round 96: about 3.5 points less bad over 20 sessions (t 2.8, "一般"); not yet a money-maker after costs.
Only tradable stocks (20-session median traded value >= A$20k), announcements from the last 10 calendar days.
Both lists are written into the live track record (out/track_au_ann.json, append-only; previous copy restored from the
quick-results branch first) so in a few months we know whether round 96 holds with real dates.
"""
import json
import os
import statistics
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import study

AI = study.DATA / "ai" / "s76"
OUT = Path(os.environ.get("ANNLISTS_OUT", "out/ann_lists.json"))
TYPES = {"drilling_assay": "钻探/化验", "resource_reserve": "资源量", "capital_raising": "融资", "quarterly_report": "季报",
         "financial_results": "业绩", "contract_order": "合同/订单", "acquisition_divestment": "收购/出售",
         "permit_approval": "许可/批准", "clinical_regulatory": "临床/监管", "takeover_scheme": "收购要约",
         "production_update": "生产", "other": "其他"}


def restore_track():
    """Bring back the previous track file (append-only record) before adding today's entries."""
    p = Path("out/track_au_ann.json")
    if p.exists() or not os.environ.get("ASX_DATA_KEY"):
        return
    try:
        import securedata
        url = "https://raw.githubusercontent.com/pbz13p/asx-terminal/quick-results/track_au_ann.json.enc"
        with urllib.request.urlopen(url, timeout=30) as r:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(securedata.decrypt(r.read()))
    except Exception as e:
        print("[ann] no earlier track file:", type(e).__name__)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    since = (date.today() - timedelta(days=10)).isoformat()
    scores = []
    for f in AI.glob("*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if s.get("date", "") >= since and s.get("code"):
            s["id"] = f.stem
            scores.append(s)
    print("[ann] scored in the last 10 days:", len(scores))
    ix = study.load("AXSO.INDX")
    bench = ix[-1][4] if ix else None
    avoid, good = [], []
    for s in scores:
        rows = study.load(f"{s['code']}.AU")
        if len(rows) < 25:
            continue
        ds = [r[0] for r in rows]
        import bisect
        i = bisect.bisect_left(ds, s["date"])          # announcement day (or the next session)
        k = min(i, len(rows) - 1)
        if statistics.median(r[6] * r[5] for r in rows[max(0, k - 20):k] or rows[-20:]) < 20_000 or rows[-1][6] < 0.01:
            continue
        before = rows[i - 1][4] if i > 0 else None
        j = bisect.bisect_right(ds, s["date"])          # first session after the announcement day = when we could act
        imp, hype, dil = num(s.get("importance")) or 0, num(s.get("hype")) or 0, num(s.get("dilution_risk")) or 0
        item = {"code": s["code"], "date": s["date"], "type": TYPES.get(s.get("type"), s.get("type") or ""), "importance": imp,
                "vs_prior": s.get("vs_prior"), "hype": hype, "dilution": dil, "cash_q": num(s.get("cash_quarters")),
                "one_line": (s.get("one_line") or "")[:160], "price": rows[-1][6],
                "since_pct": round(100 * (rows[-1][4] / before - 1), 1) if before else None,
                "entry_px": rows[j][6] if j < len(rows) else None, "entry_day": rows[j][0] if j < len(rows) else None}
        why = []
        if dil >= 3:
            why.append("大稀释")
        if s.get("type") == "capital_raising":
            why.append("融资")
        if hype >= 3:
            why.append("吹嘘")
        if why:
            avoid.append(dict(item, why="、".join(why)))
        if imp >= 4 and s.get("vs_prior") == "above" and hype <= 1 and dil <= 1:
            good.append(item)
    avoid.sort(key=lambda x: x["date"], reverse=True)
    good.sort(key=lambda x: x["date"], reverse=True)
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "since": since, "avoid": avoid[:150], "good": good[:80],
           "note": "第 96 轮：不碰名单之后 20 天平均比别的公告多亏 3–4 个点；干净好消息少亏约 3.5 个点（一般，扣成本后仍没跑赢小盘指数）。"}
    restore_track()
    try:
        import tracklog
        for lst, rows_ in (("ann_good", good), ("ann_avoid", avoid)):
            by_day = {}
            for r in rows_:
                if r["entry_day"] and r["entry_px"]:
                    by_day.setdefault(r["entry_day"], []).append({"code": r["code"], "px": r["entry_px"]})
            ixd = {r[0]: r[4] for r in ix}
            for d, items in sorted(by_day.items()):
                tracklog.record("au_ann", lst, d, items, ixd.get(d, bench))

        def since_ret(code, day):
            rows = study.load(f"{code}.AU")
            return tracklog.adj_return([(r[0], r[4]) for r in rows if r[4]], day)
        out["track"] = tracklog.summarise("au_ann", since_ret, bench, {"ann_good": "干净好消息", "ann_avoid": "不碰名单"})
    except Exception as e:
        print("[ann] track record failed:", type(e).__name__, e)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("[ann] lists written:", len(avoid), "avoid,", len(good), "good")


if __name__ == "__main__":
    main()
