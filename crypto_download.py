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
SYMBOLS = os.environ.get("CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT").split(",")
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
    for name, url in names:
        f = folder / name
        if f.exists() and f.stat().st_size > 0:
            got += 1
            continue
        if (folder / (name + ".none")).exists() and name.count("-") == 3:   # a past month that was never published
            continue
        body = get(url + name)
        if body is None:
            (folder / (name + ".none")).write_text("")   # not published (coin not listed yet)
            continue
        f.write_bytes(body)
        got += 1
        new += 1
        time.sleep(0.2)   # polite
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
    summary = {}
    for sym in SYMBOLS:
        k_got, k_new = fetch("klines", sym)
        f_got, f_new = fetch("fundingRate", sym)
        K, F = rows("klines", sym), rows("fundingRate", sym)
        (DATA / f"{sym}_1h.json").write_text(json.dumps(K))
        (DATA / f"{sym}_funding.json").write_text(json.dumps(F))
        summary[sym] = {"hours": len(K), "first": time.strftime("%Y-%m-%d", time.gmtime(K[0][0])) if K else None,
                        "last": time.strftime("%Y-%m-%d %H:00", time.gmtime(K[-1][0])) if K else None,
                        "funding_points": len(F), "files_new": k_new + f_new}
        print("[crypto]", sym, summary[sym], flush=True)   # public market data: counts and dates only
    (DATA / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
