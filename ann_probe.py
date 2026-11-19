"""One-off probe: how far back does the market-wide announcements feed (markitdigital) page? Prints dates/counts only."""
import json
import urllib.request

UA = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
B = "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements"
for q in ["?itemsPerPage=100&page=0", "?itemsPerPage=100&page=50", "?itemsPerPage=100&page=500", "?itemsPerPage=100&page=5000",
          "?itemsPerPage=100&page=0&dateFrom=2019-03-01&dateTo=2019-03-02", "?itemsPerPage=100&page=0&startDate=2019-03-01&endDate=2019-03-02"]:
    try:
        with urllib.request.urlopen(urllib.request.Request(B + q, headers=UA), timeout=40) as r:
            js = json.load(r)
        it = (js.get("data") or {}).get("items", [])
        ks = list(it[0].keys()) if it else []
        dts = sorted(str(x.get("date") or x.get("documentDate") or x.get("releaseDate") or "") for x in it)
        print(f"[probe] {q}: {len(it)} items, keys {ks[:10]}, dates {dts[:1]} .. {dts[-1:]}, meta {list((js.get('data') or {}).keys())}")
    except Exception as e:
        print(f"[probe] {q}: {type(e).__name__} {str(e)[:80]}")
