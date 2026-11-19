"""One-off probe: can announcements of DELISTED ASX companies be listed anywhere official? Prints status + counts only."""
import json
import re
import urllib.request

UA = {"Accept": "text/html,application/json,*/*", "User-Agent": "Mozilla/5.0"}
TESTS = [
    ("asx by code 4CE 2019", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=4CE&timeframe=Y&year=2019"),
    ("asx by code BHP 2019 (control)", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=BHP&timeframe=Y&year=2019"),
    ("asx by code ABP 2021 (renamed ABG)", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=ABP&timeframe=Y&year=2021"),
    ("markit 4CE announcements", "https://asx.api.markitdigital.com/asx-research/1.0/companies/4ce/announcements?count=50"),
    ("markit 4CE header", "https://asx.api.markitdigital.com/asx-research/1.0/companies/4ce/header"),
    ("markit market anns", "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements?itemsPerPage=50&page=0"),
    ("asx today anns", "https://www.asx.com.au/asx/v2/statistics/todayAnns.do"),
    ("asx prev day anns", "https://www.asx.com.au/asx/v2/statistics/prevBusDayAnns.do"),
    ("asx by date?", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=date&date=15/03/2019"),
    ("asx delisted 4CE page", "https://www.asx.com.au/markets/trade-our-cash-market/historical-company-data/delisted"),
]
for name, url in TESTS:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
            txt = body.decode("utf-8", "replace")
            rows = len(re.findall(r"idsId=\d+", txt))
            js = None
            try:
                js = json.loads(txt)
            except Exception:
                pass
            keys = list(js.keys())[:6] if isinstance(js, dict) else None
            items = None
            if isinstance(js, dict):
                dd = js.get("data") or {}
                items = len(dd.get("items", [])) if isinstance(dd, dict) else None
            print(f"[probe] {name}: HTTP {r.status}, {len(body)} bytes, pdf links {rows}, json keys {keys}, items {items}")
    except Exception as e:
        print(f"[probe] {name}: {type(e).__name__} {str(e)[:80]}")
