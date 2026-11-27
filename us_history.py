"""US stocks: full daily price history for every US-listed common stock and ETF, delisted ones included
(Frank 2026-11-27: "美股、比特币、澳股、A 股都下来" -- download everything not yet downloaded).

What it is for: the same research we run on ASX (round 80 ten-baggers, round 82 one-year-100x, round 98 bottom +
breakout) on a much bigger market with survivorship-free data; the 🇺🇸 美股 tab's long charts.

Source: EODHD (already paid; the only primary source for US daily bars, CLAUDE.md). One API call per symbol for the
full history; delisted symbols from exchange-symbol-list/US?delisted=1. OTC / pink sheets are left out (thin, not
tradable at IBKR for Frank's size). Calls are capped per UTC day (US_MAX_CALLS, default 80,000 of the plan's 100,000) so the ASX and A-share
jobs keep their share of the daily EODHD allowance; unfinished symbols carry over to the next run.
After a symbol has its full history, us_daily.py's whole-market snapshot keeps it current (no extra calls).
Output: data/us_hist/<CODE>.json.gz  rows [date, open, high, low, close, adjusted_close, volume]; summary in
data/us_hist/_summary.json. Kept in the Actions cache (us-hist-*), weekly encrypted backup (backup.yml).
"""
import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KEY = os.environ.get("EODHD_API_KEY", "")
OUT = Path("data/us_hist")
EXCH = {"NYSE", "NASDAQ", "NYSE ARCA", "NYSE MKT", "AMEX", "BATS", "NYSE American"}
TYPES = {"Common Stock", "ETF"}
MAX_CALLS = int(os.environ.get("US_MAX_CALLS", "80000"))
BUDGET = float(os.environ.get("US_MINUTES", "320")) * 60


def api(path, **params):
    params.update(api_token=KEY, fmt="json")
    url = f"https://eodhd.com/api/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.load(r), r.status
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(6 * (attempt + 1))
                continue
            return None, e.code
        except (urllib.error.URLError, TimeoutError):
            time.sleep(6 * (attempt + 1))
    return None, 0


def universe():
    out, seen = [], set()
    for delisted in (0, 1):
        rows, st = api("exchange-symbol-list/US", **({"delisted": 1} if delisted else {}))
        for r in rows or []:
            code = r.get("Code")
            if not code or code in seen or r.get("Type") not in TYPES:
                continue
            if (r.get("Exchange") or "") not in EXCH:
                continue
            seen.add(code)
            out.append((code, bool(delisted), r.get("Type"), r.get("Exchange"), r.get("Name")))
    return out


def probe():
    """What else the EODHD plan gives (answers 'is there anything we are not downloading?'). 1-3 calls."""
    res = {}
    for name, path, kw in (("intraday_1h", "intraday/AAPL.US", {"interval": "1h"}),
                           ("intraday_1m", "intraday/BHP.AU", {"interval": "1m"}),
                           ("fundamentals", "fundamentals/AAPL.US", {})):
        data, st = api(path, **kw)
        res[name] = {"http": st, "rows": (len(data) if isinstance(data, list) else (1 if data else 0))}
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    sf = OUT / "_summary.json"
    summ = json.loads(sf.read_text()) if sf.exists() else {"done": {}, "calls": {}}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    calls = summ["calls"].get(today, 0)
    uni = universe()
    calls += 2
    if "probe" not in summ:
        summ["probe"] = probe()
        calls += 3
    todo = [u for u in uni if u[0] not in summ["done"]]
    print(f"[us] universe {len(uni)} (delisted {sum(u[1] for u in uni)}), done {len(summ['done'])}, to do {len(todo)}, calls today {calls}", flush=True)
    lock = threading.Lock()
    n = [0]

    def job(u):
        code = u[0]
        with lock:
            if calls_box[0] >= MAX_CALLS or time.time() - t0 > BUDGET:
                return
            calls_box[0] += 1
        rows, st = api(f"eod/{code}.US", **{"from": "1900-01-01"})
        if rows is None and st not in (404,):
            return   # try again next run
        keep = [[r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("adjusted_close"), r.get("volume")]
                for r in rows or []]
        (OUT / f"{code.replace('/', '_')}.json.gz").write_bytes(gzip.compress(json.dumps(keep, separators=(",", ":")).encode()))
        with lock:
            summ["done"][code] = {"rows": len(keep), "first": keep[0][0] if keep else None, "last": keep[-1][0] if keep else None,
                                  "delisted": u[1], "type": u[2]}
            n[0] += 1
            if n[0] % 500 == 0:
                print(f"  {n[0]}/{len(todo)}  calls today {calls_box[0]}  {round((time.time() - t0) / 60)} min", flush=True)
                sf.write_text(json.dumps(summ))

    calls_box = [calls]
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(job, todo))
    summ["calls"][today] = calls_box[0]
    left = sum(1 for u in uni if u[0] not in summ["done"])
    summ["left"] = left
    summ["universe"] = len(uni)
    sf.write_text(json.dumps(summ))
    Path("us_left.txt").write_text(str(left if calls_box[0] < MAX_CALLS else 0))   # cap hit: wait for tomorrow's schedule
    done = summ["done"].values()
    print(json.dumps({"universe": len(uni), "done": len(summ["done"]), "left": left, "calls_today": calls_box[0],
                      "with_rows": sum(1 for d in done if d["rows"]), "before_2000": sum(1 for d in done if d["first"] and d["first"] < "2000"),
                      "delisted_done": sum(1 for d in done if d["delisted"]), "probe": summ.get("probe"),
                      "minutes": round((time.time() - t0) / 60)}), flush=True)


if __name__ == "__main__":
    main()
