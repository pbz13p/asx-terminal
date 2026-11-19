"""One-off probe: do renamed companies' pre-rename announcements show under the new code anywhere? (CND, ex GLV). Dates/counts only."""
import json
import re
import urllib.request

UA = {"Accept": "text/html,application/json,*/*", "User-Agent": "Mozilla/5.0"}
for name, url in [
    ("asx GLV 2020", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=GLV&timeframe=Y&year=2020"),
    ("asx GLV 2023", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=GLV&timeframe=Y&year=2023"),
    ("asx CND 2023", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=CND&timeframe=Y&year=2023"),
    ("asx CND 2020", "https://www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=CND&timeframe=Y&year=2020"),
    ("markit CND", "https://asx.api.markitdigital.com/asx-research/1.0/companies/CND/announcements"),
    ("markit cnd", "https://asx.api.markitdigital.com/asx-research/1.0/companies/cnd/announcements"),
    ("markit CND count", "https://asx.api.markitdigital.com/asx-research/1.0/companies/CND/announcements?count=500&market_sensitive=false"),
    ("markit CND header", "https://asx.api.markitdigital.com/asx-research/1.0/companies/CND/header"),
    ("markit GLV", "https://asx.api.markitdigital.com/asx-research/1.0/companies/GLV/announcements"),
]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            txt = r.read().decode("utf-8", "replace")
        dates = re.findall(r"(\d{2}/\d{2}/\d{4})", txt) if "asx.com.au" in url else re.findall(r'"date":"(\d{4}-\d{2}-\d{2})', txt)
        heads = len(re.findall(r"idsId=\d+", txt)) if "asx.com.au" in url else txt.count('"documentKey"')
        extra = ""
        try:
            js = json.loads(txt)
            dd = js.get("data") or {}
            extra = f" keys {list(dd.keys())[:12]}" if isinstance(dd, dict) else ""
        except Exception:
            pass
        print(f"[probe] {name}: {heads} docs, dates {sorted(dates)[:1]}..{sorted(dates)[-1:]}{extra}")
    except Exception as e:
        print(f"[probe] {name}: {type(e).__name__} {str(e)[:60]}")
