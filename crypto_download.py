"""Round 86 data: crypto perpetual futures (Frank 2026-11-18: "collect the data, run strategies, trade on the strategy").

What it tests: chart-reading / trend / breakout rules on BTC, ETH and other large perpetuals, net of real costs
(taker fee + funding every 8 hours + slippage), with liquidation risk at each leverage (ROADMAP round 86).

Source: Binance's public bulk-data site data.binance.vision (no login, no key): USDT-margined perpetual 1-hour klines
and funding-rate history, one zip per month (daily zips for the current month). Each file is fetched once and kept in
data/crypto/raw; data/crypto/<SYMBOL>_1h.json and <SYMBOL>_funding.json are rebuilt from them.
"""
import csv
import io
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

DATA = Path("data/crypto")
RAW = DATA / "raw"
BASE = "https://data.binance.vision/data/futures/um"
SYMBOLS = [x for x in os.environ.get("CRYPTO_SYMBOLS", "").split(",") if x]   # empty = every USDT perpetual ever listed
LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/futures/um/monthly/klines/"
BUDGET = float(os.environ.get("CRYPTO_MINUTES", "300")) * 60
START = date(2019, 9, 1)


def get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (attempt + 1))
    return None


def all_symbols():
    """Every USDT-margined perpetual Binance has published data for -- delisted ones included (no survivorship)."""
    import re
    out, marker = [], ""
    while True:
        body = get(LIST + (f"&marker={marker}" if marker else ""))
        if body is None:
            break
        page = body.decode()
        got = re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>", page)
        out += got
        if "<IsTruncated>true</IsTruncated>" not in page or not got:
            break
        marker = f"data/futures/um/monthly/klines/{got[-1]}/"
    return sorted({s for s in out if s.endswith("USDT") and "_" not in s})   # perpetuals only (no dated quarterlies)


def months(sym):
    d = START
    today = date.today()
    while (d.year, d.month) < (today.year, today.month):
        yield f"{d.year}-{d.month:02d}"
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def fetch(kind, sym):
    """kind: klines (1h) or fundingRate. Monthly zips for past months, daily zips for this month."""
    got = new = 0
    folder = RAW / kind / sym
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for m in months(sym):
        names.append((f"{sym}-1h-{m}.zip" if kind == "klines" else f"{sym}-fundingRate-{m}.zip",
                      f"{BASE}/monthly/{kind}/{sym}/1h/" if kind == "klines" else f"{BASE}/monthly/{kind}/{sym}/"))
    if kind == "klines":   # this month, day by day (yesterday back to the 1st)
        d = date.today().replace(day=1)
        while d < date.today():
            names.append((f"{sym}-1h-{d.isoformat()}.zip", f"{BASE}/daily/klines/{sym}/1h/"))
            d += timedelta(days=1)
    todo = []
    for name, url in names:
        f = folder / name
        if f.exists() and f.stat().st_size > 0:
            got += 1
        elif not ((folder / (name + ".none")).exists() and name.count("-") == 3):   # past months never published: skip
            todo.append((name, url))

    def one(nu):
        name, url = nu
        body = get(url + name)
        if body is None:
            (folder / (name + ".none")).write_text("")   # not published (coin not listed then / delisted)
            return 0
        (folder / name).write_bytes(body)
        return 1
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        for k in ex.map(one, todo):
            got += k
            new += k
    return got, new


def rows(kind, sym):
    out = {}
    for f in sorted((RAW / kind / sym).glob("*.zip")):
        try:
            z = zipfile.ZipFile(f)
        except zipfile.BadZipFile:
            continue
        for n in z.namelist():
            for r in csv.reader(io.TextIOWrapper(z.open(n), encoding="utf-8")):
                if not r or not r[0][:1].isdigit():
                    continue   # header line in newer files
                if kind == "klines":   # open_time, open, high, low, close, volume, close_time, quote_volume, trades, ...
                    t = int(r[0]) // (1000 if len(r[0]) <= 13 else 1000000)
                    out[t] = [t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
                else:   # calc_time, funding_interval_hours, last_funding_rate
                    t = int(r[0]) // 1000
                    out[t] = [t, float(r[-1])]
    return [out[k] for k in sorted(out)]


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    syms = SYMBOLS or all_symbols()
    print("[crypto] symbols", len(syms), flush=True)
    sf = DATA / "summary.json"
    summary = json.loads(sf.read_text()) if sf.exists() else {}
    left = 0
    for sym in syms:
        if time.time() - t0 > BUDGET:
            left += 1
            continue
        k_got, k_new = fetch("klines", sym)
        f_got, f_new = fetch("fundingRate", sym)
        K, F = rows("klines", sym), rows("fundingRate", sym)
        (DATA / f"{sym}_1h.json").write_text(json.dumps(K))
        (DATA / f"{sym}_funding.json").write_text(json.dumps(F))
        summary[sym] = {"hours": len(K), "first": time.strftime("%Y-%m-%d", time.gmtime(K[0][0])) if K else None,
                        "last": time.strftime("%Y-%m-%d %H:00", time.gmtime(K[-1][0])) if K else None,
                        "funding_points": len(F), "files_new": k_new + f_new}
    sf.write_text(json.dumps(summary, indent=1))
    Path("crypto_left.txt").write_text(str(left))
    done = [v for v in summary.values() if v["hours"]]
    print("[crypto] symbols with data", len(done), "left for next run", left, "minutes", round((time.time() - t0) / 60), flush=True)


if __name__ == "__main__":
    main()
