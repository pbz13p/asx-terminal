"""China A-share price archive from EODHD (pbz's key): every common stock on Shanghai (SHG) and Shenzhen (SHE),
listed and delisted, full history. Incremental: existing files only fetch new days.
Output: data/cn/{code}.{EX}.json.gz  rows [date, open, high, low, close, adjusted_close, volume]
        data/cn/universe.json        symbol list with name, exchange, delisted flag
"""
import concurrent.futures as cf
import gzip
import json
import os
import time
import urllib.request
from pathlib import Path

KEY = os.environ.get("EODHD_API_KEY", "")
OUT = Path("data/cn")
BASE = "https://eodhd.com/api"


def get(url, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(2 + 3 * k)


def universe():
    items = []
    for ex in ("SHG", "SHE"):
        for delisted in (0, 1):
            rows = get(f"{BASE}/exchange-symbol-list/{ex}?api_token={KEY}&fmt=json" + ("&delisted=1" if delisted else ""))
            for r in rows:
                if r.get("Type") == "Common Stock":
                    items.append({"code": r["Code"], "ex": ex, "name": r.get("Name"), "isin": r.get("Isin"), "delisted": bool(delisted)})
    seen, uniq = set(), []
    for it in items:
        k = (it["code"], it["ex"])
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq


def fetch(it):
    f = OUT / f"{it['code']}.{it['ex']}.json.gz"
    old = json.loads(gzip.decompress(f.read_bytes())) if f.exists() else []
    start = old[-1][0] if old else "1990-01-01"
    rows = get(f"{BASE}/eod/{it['code']}.{it['ex']}?api_token={KEY}&fmt=json&from={start}")
    new = [[r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("adjusted_close"), r.get("volume")]
           for r in rows if not old or r["date"] > old[-1][0]]
    if old and new and start != "1990-01-01":
        # adjusted prices change after a split/dividend: refetch everything if the overlap day moved
        ov = next((r for r in rows if r["date"] == old[-1][0]), None)
        if ov and old[-1][5] and abs((ov.get("adjusted_close") or 0) / old[-1][5] - 1) > 1e-4:
            rows = get(f"{BASE}/eod/{it['code']}.{it['ex']}?api_token={KEY}&fmt=json&from=1990-01-01")
            old, new = [], [[r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("adjusted_close"), r.get("volume")] for r in rows]
    if new or not f.exists():
        f.write_bytes(gzip.compress(json.dumps(old + new, separators=(",", ":")).encode()))
    return len(new)


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    U = universe()
    (OUT / "universe.json").write_text(json.dumps(U, ensure_ascii=False))
    print("[universe]", len(U), "stocks,", sum(u["delisted"] for u in U), "delisted")
    added = failed = 0
    fails = []
    with cf.ThreadPoolExecutor(8) as pool:
        futs = {pool.submit(fetch, it): it for it in U}
        for i, fu in enumerate(cf.as_completed(futs)):
            try:
                added += fu.result()
            except Exception as e:
                failed += 1
                fails.append(f"{futs[fu]['code']}.{futs[fu]['ex']} {type(e).__name__}")
            if i % 500 == 0:
                print("[fetch]", i, len(U), f"{time.time() - t0:.0f}s")
    files = list(OUT.glob("*.json.gz"))
    size = sum(f.stat().st_size for f in files)
    summary = {"stocks": len(U), "delisted": sum(u["delisted"] for u in U), "files": len(files), "rows_added": added,
               "failed": failed, "failed_list": fails[:50], "size_mb": round(size / 1e6, 1), "seconds": round(time.time() - t0)}
    (OUT / "summary.json").write_text(json.dumps(summary))
    print(json.dumps(summary))
    out = os.environ.get("STUDYCN_OUT")
    if out:
        Path(out).write_text(json.dumps(summary))


if __name__ == "__main__":
    main()
