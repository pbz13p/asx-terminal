"""US market: keep our own daily snapshot of every US-listed security (EODHD whole-exchange end-of-day file, about
100 API credits a day). History can be downloaded any time later; this archive is ours from the first day it runs.
Output: data/us_daily/YYYY-MM-DD.json.gz (cached in Actions, encrypted copy kept on the cn-results branch is not needed:
these are raw prices, kept private in the Actions cache)."""
import gzip
import json
import os
import urllib.request
from pathlib import Path

KEY = os.environ.get("EODHD_API_KEY", "")
OUT = Path("data/us_daily")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"https://eodhd.com/api/eod-bulk-last-day/US?api_token={KEY}&fmt=json&filter=extended", timeout=300) as r:
        rows = json.loads(r.read())
    if not rows:
        print("[us] empty")
        return
    day = max(x.get("date", "") for x in rows)
    f = OUT / f"{day}.json.gz"
    f.write_bytes(gzip.compress(json.dumps(rows, separators=(",", ":")).encode()))
    days = sorted(p.name[:10] for p in OUT.glob("*.json.gz"))
    print(json.dumps({"day": day, "securities": len(rows), "days_archived": len(days), "first": days[0]}))


if __name__ == "__main__":
    main()
