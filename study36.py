"""Round 36: how far back does the free ASX announcement API go? (decides whether real event studies are possible)"""
import json
import os
import urllib.request
from pathlib import Path

H = {"Accept": "application/json"}
TRIES = [
    "https://asx.api.markitdigital.com/asx-research/1.0/companies/{c}/announcements?count=500&market_sensitive=false",
    "https://asx.api.markitdigital.com/asx-research/1.0/companies/{c}/announcements?count=100&page=5",
    "https://asx.api.markitdigital.com/asx-research/1.0/companies/{c}/announcements?itemsPerPage=100&page=3",
    "https://asx.api.markitdigital.com/asx-research/1.0/companies/{c}/announcements?year=2019",
    "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements?page=200&itemsPerPage=100",
    "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode={c}&timeframe=Y&year=2019",
]


def main():
    out = {}
    for c in ("BHP", "PDN"):
        for k, u in enumerate(TRIES):
            url = u.format(c=c)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                info = {"ok": True, "len": len(body)}
                try:
                    items = json.loads(body)["data"]["items"]
                    ds = sorted(x.get("date", "")[:10] for x in items)
                    info.update(n=len(items), first=ds[0] if ds else None, last=ds[-1] if ds else None)
                except Exception:
                    import re
                    ds = sorted(set(re.findall(r"\d{2}/\d{2}/\d{4}", body)))
                    info.update(html_dates=len(ds), sample=ds[:3] + ds[-3:])
            except Exception as e:
                info = {"ok": False, "err": f"{type(e).__name__}: {str(e)[:100]}"}
            out[f"{c}#{k}"] = info
    p = Path(os.environ.get("STUDY36_OUT", "data/study36_results.json"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
