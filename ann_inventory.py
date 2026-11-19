"""Inventory of the private ASX announcement archive (data/ann), so Frank can check any company.

For every company: announcements ASX lists for it (asx.com.au yearly lists we stored), how many we hold with text,
how many failed after 3 tries, how many are still missing, years covered, and count per year. Also flags gaps:
a year inside the company's trading life with no announcements listed at all.
Output out/ann_inventory.json (encrypted by the workflow; never published in plain text).
"""
import gzip
import json
import time
from collections import Counter
from pathlib import Path

ANN = Path("data/ann")
EOD = Path("data/eod")


def main():
    rows, tot = [], Counter()
    for f in sorted(ANN.glob("*.json.gz")):
        try:
            d = json.loads(gzip.decompress(f.read_bytes()))
        except Exception:
            continue
        code = f.name[:-8]
        listed = d.get("listed", [])
        docs = d.get("docs", {})
        ok = sum(1 for x in docs.values() if "error" not in x)
        err = sum(1 for x in docs.values() if "error" in x)
        miss = sum(1 for it in listed if it["ids"] not in docs)
        sens = sum(1 for it in listed if it.get("sensitive"))
        per_year = Counter(it["date"][:4] for it in listed if it.get("date"))
        ok_year = Counter(x["date"][:4] for x in docs.values() if "error" not in x and x.get("date"))
        trade = None
        p = EOD / f"{code}.AU.json"
        if p.exists():
            try:
                r = json.loads(p.read_text())
                trade = (r[0][0][:4], r[-1][0][:4]) if r else None
            except Exception:
                pass
        gaps = []
        if trade:
            lo, hi = max(int(trade[0]), 2015), min(int(trade[1]), int(time.strftime("%Y")))
            gaps = [str(y) for y in range(lo, hi + 1) if per_year.get(str(y), 0) == 0]
        rows.append({"code": code, "listed": len(listed), "ok": ok, "failed": err, "missing": miss, "sensitive": sens,
                     "first": min(per_year) if per_year else None, "last": max(per_year) if per_year else None,
                     "trading": trade, "gap_years": gaps, "years_done": sorted(d.get("years_done", [])),
                     "per_year": dict(sorted(per_year.items())), "ok_per_year": dict(sorted(ok_year.items()))})
        tot["companies"] += 1
        tot["listed"] += len(listed)
        tot["ok"] += ok
        tot["failed"] += err
        tot["missing"] += miss
        tot["no_list"] += not listed
        tot["with_gaps"] += bool(gaps)
    out = {"generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "totals": dict(tot), "companies": rows}
    Path("out").mkdir(exist_ok=True)
    Path("out/ann_inventory.json").write_text(json.dumps(out, ensure_ascii=False))
    print("[inventory] companies", tot["companies"], "done", flush=True)   # numbers stay encrypted


if __name__ == "__main__":
    main()
