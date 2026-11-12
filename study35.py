"""Round 35: probe free ASX announcement feeds (for a takeover/scheme and placement scanner)."""
import json
import os
import urllib.request
from pathlib import Path

HEADERS = {"Accept": "application/json"}
URLS = {
    "company_ann": "https://asx.api.markitdigital.com/asx-research/1.0/companies/BHP/announcements?count=20&market_sensitive=false",
    "company_ann_v2": "https://asx.api.markitdigital.com/asx-research/1.0/companies/BHP/announcements",
    "market_today": "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements?page=0&itemsPerPage=50",
    "market_today_v2": "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements",
    "legacy_prev_business_day": "https://www.asx.com.au/asx/v2/statistics/prevBusDayAnns.do",
    "legacy_today": "https://www.asx.com.au/asx/v2/statistics/todayAnns.do",
    "legacy_company": "https://www.asx.com.au/asx/1/company/BHP/announcements?count=20&market_sensitive=false",
}


def main():
    out = {}
    for k, u in URLS.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=HEADERS), timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            out[k] = {"ok": True, "len": len(body), "head": body[:1500]}
        except Exception as e:
            out[k] = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:150]}"}
    p = Path(os.environ.get("STUDY35_OUT", "data/study35_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: (v["ok"], v.get("len"), v.get("err")) for k, v in out.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
