"""A-share 龙虎榜 (Dragon-Tiger list) archive from East Money's public data centre (the same source AKShare uses).

Four tables, fetched month by month with a date filter, stored as data/cn_lhb/{table}/{YYYY-MM}.json.gz:
  list   RPT_DAILYBILLBOARD_DETAILSNEW      每日上榜个股：上榜原因、龙虎榜买入/卖出/净买额、换手、流通市值
  org    RPT_ORGANIZATION_TRADE_DETAILS     机构席位买卖
  buy    RPT_BILLBOARD_DAILYDETAILSBUY      买入前五营业部（游资席位）
  sell   RPT_BILLBOARD_DAILYDETAILSSELL     卖出前五营业部
Note: the list table also carries East Money's own after-the-fact returns (D1..D10_CLOSE_ADJCHRATE); research must
never use them as features. Finished months are not fetched again; the last two months are refreshed every run.
Polite: one request at a time, short pause. Env CNLHB_MINUTES (default 140).
"""
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date

import study

OUT = study.DATA / "cn_lhb"
URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TABLES = {"list": "RPT_DAILYBILLBOARD_DETAILSNEW",
          "org": "RPT_ORGANIZATION_TRADE_DETAILS",
          "buy": "RPT_BILLBOARD_DAILYDETAILSBUY",
          "sell": "RPT_BILLBOARD_DAILYDETAILSSELL"}
FIELD = "TRADE_DATE"
START = (2005, 1)
HEADERS = {"Referer": "https://data.eastmoney.com/"}


def get(report, a, b, page):
    q = {"reportName": report, "columns": "ALL", "pageSize": "500", "pageNumber": str(page), "sortColumns": FIELD,
         "sortTypes": "1", "source": "WEB", "client": "WEB", "filter": f"({FIELD}>='{a}')({FIELD}<='{b}')"}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers=HEADERS)
    for k in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except Exception:
            if k == 3:
                raise
            time.sleep(5 * (k + 1))


def months():
    y, m = START
    t = date.today()
    while (y, m) <= (t.year, t.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main():
    t0 = time.time()
    budget = float(os.environ.get("CNLHB_MINUTES", "140")) * 60
    t = date.today()
    recent = {(t.year, t.month), (t.year - (t.month == 1), 12 if t.month == 1 else t.month - 1)}
    stats, left = {}, 0
    for name, report in TABLES.items():
        d = OUT / name
        d.mkdir(parents=True, exist_ok=True)
        got = rows = 0
        for y, m in months():
            f = d / f"{y}-{m:02d}.json.gz"
            if f.exists() and (y, m) not in recent:
                continue
            if time.time() - t0 > budget:
                left += 1
                continue
            a, b = f"{y}-{m:02d}-01", f"{y}-{m:02d}-31"
            try:
                j = get(report, a, b, 1)
                res = j.get("result") or {}
                data = list(res.get("data") or [])
                for p in range(2, (res.get("pages") or 1) + 1):
                    data += (get(report, a, b, p).get("result") or {}).get("data") or []
                    time.sleep(0.3)
            except Exception as e:
                print("failed", name, y, m, type(e).__name__)
                left += 1
                continue
            f.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode()))
            got += 1
            rows += len(data)
            time.sleep(0.3)
        files = sorted(d.glob("*.json.gz"))
        stats[name] = {"months_fetched_now": got, "rows_now": rows, "months_stored": len(files)}
        if files:
            last = json.loads(gzip.decompress(files[-1].read_bytes()))
            stats[name]["latest_columns"] = sorted(last[0].keys())[:60] if last else []
    print("[lhb]", json.dumps({"stats": stats, "left": left, "minutes": round((time.time() - t0) / 60, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
