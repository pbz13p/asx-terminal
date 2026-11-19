"""One-off probe: which commodity / futures series does our EODHD plan give, and how far back? Prints symbols + dates only."""
import json
import os
import urllib.request

K = os.environ["EODHD_API_KEY"]


def get(path, **q):
    q.update(api_token=K, fmt="json")
    url = "https://eodhd.com/api/" + path + "?" + "&".join(f"{a}={b}" for a, b in q.items())
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    except Exception as e:
        return f"ERR {type(e).__name__} {str(e)[:60]}"


for ex in ("COMM", "CC", "FOREX"):
    x = get(f"exchange-symbol-list/{ex}")
    if isinstance(x, list):
        print(f"[probe] exchange {ex}: {len(x)} symbols; sample {[s.get('Code') for s in x[:40]]}")
    else:
        print(f"[probe] exchange {ex}: {x}")
for s in ["GC.COMM", "CL.COMM", "XAUUSD.FOREX", "XAGUSD.FOREX", "BZ.COMM", "NG.COMM", "HG.COMM", "GC=F.COMM", "BRENT.COMM",
          "WTI.COMM", "GOLD.COMM", "XPTUSD.FOREX", "XPDUSD.FOREX", "IRON.COMM", "ZS.COMM"]:
    x = get(f"eod/{s}", **{"from": "1900-01-01"})
    print(f"[probe] {s}: " + (f"{len(x)} rows {x[0]['date']}..{x[-1]['date']}" if isinstance(x, list) and x else str(x)[:80]))
