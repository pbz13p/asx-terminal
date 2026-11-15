"""A-share data that EODHD lacks, from free sources, for factor research (round 77+). Resumable; runs on GitHub Actions.

1. Financial statements (东方财富数据中心, the source AKShare's stock_yjbb_em / zcfz / lrb / xjll use), one request set
   per report quarter for the whole market, 2005 onwards, with NOTICE_DATE (when the market learned it -> no look-ahead):
     data/cn_fin/{yjbb|balance|income|cashflow}/{YYYY-MM-DD}.json.gz
2. Baostock (free, anonymous login):
   - industry classification (证监会行业) for every stock:     data/cn_val/_industry_{date}.json.gz
   - stock list incl. delisted:                               data/cn_val/_basic.json.gz
   - daily turnover rate, traded value (amount -> true VWAP), PE TTM, PB, PS TTM, PCF TTM, ST flag, trading status,
     2008 onwards, one file per stock:                        data/cn_val/{sh|sz}.{code}.json.gz
     Float market cap = close x volume / turnover rate.
Minute bars (for 聪明钱 and other intraday factors) are a separate step: too big to keep raw (see ROADMAP).
Env CNX_MINUTES (default 300), CNX_PART (fin,val; default both).
"""
import gzip
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date

import study

FIN = study.DATA / "cn_fin"
VAL = study.DATA / "cn_val"
URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SEC = '(SECURITY_TYPE_CODE in ("058001001","058001008"))(TRADE_MARKET_CODE!="069001017")'
FIN_TABLES = {"yjbb": ("RPT_LICO_FN_CPD", "REPORTDATE", ""), "balance": ("RPT_DMSK_FN_BALANCE", "REPORT_DATE", SEC),
              "income": ("RPT_DMSK_FN_INCOME", "REPORT_DATE", SEC), "cashflow": ("RPT_DMSK_FN_CASHFLOW", "REPORT_DATE", SEC)}
VAL_FIELDS = "date,close,volume,amount,turn,tradestatus,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"  # only what EODHD lacks; close+volume kept to derive market cap and to spot-check EODHD
T0 = time.time()
BUDGET = float(os.environ.get("CNX_MINUTES", "300")) * 60


def left():
    return BUDGET - (time.time() - T0)


def em(report, flt, page):
    q = {"reportName": report, "columns": "ALL", "pageSize": "500", "pageNumber": str(page), "sortColumns": "SECURITY_CODE",
         "sortTypes": "1", "source": "WEB", "client": "WEB", "filter": flt}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers={"Referer": "https://data.eastmoney.com/"})
    for k in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except Exception:
            if k == 3:
                raise
            time.sleep(5 * (k + 1))


def quarters():
    t = date.today()
    for y in range(2005, t.year + 1):
        for md in ("03-31", "06-30", "09-30", "12-31"):
            d = f"{y}-{md}"
            if d <= t.isoformat():
                yield d


def fin():
    recent = list(quarters())[-4:]
    stats = {}
    for name, (report, field, extra) in FIN_TABLES.items():
        d = FIN / name
        d.mkdir(parents=True, exist_ok=True)
        got = rows = 0
        for q in quarters():
            f = d / f"{q}.json.gz"
            if (f.exists() and q not in recent) or left() < 60:
                continue
            flt = f"{extra}({field}='{q}')"
            try:
                j = em(report, flt, 1)
                res = j.get("result") or {}
                data = list(res.get("data") or [])
                for p in range(2, (res.get("pages") or 1) + 1):
                    data += (em(report, flt, p).get("result") or {}).get("data") or []
                    time.sleep(0.3)
            except Exception as e:
                print("fin failed", name, q, type(e).__name__)
                continue
            f.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode()))
            got += 1
            rows += len(data)
            time.sleep(0.3)
        stats[name] = {"quarters_now": got, "rows_now": rows, "quarters_stored": len(list(d.glob("*.json.gz")))}
    return stats


def bs_rows(rs):
    out = []
    while rs.error_code == "0" and rs.next():
        out.append(rs.get_row_data())
    return out, rs.fields


def val():
    import baostock as bs
    VAL.mkdir(parents=True, exist_ok=True)
    lg = bs.login()
    if lg.error_code != "0":
        raise SystemExit(f"baostock login failed: {lg.error_msg}")
    today = date.today().isoformat()
    basic, fields = bs_rows(bs.query_stock_basic())
    (VAL / "_basic.json.gz").write_bytes(gzip.compress(json.dumps({"fields": fields, "rows": basic}, ensure_ascii=False).encode()))
    ind, f2 = bs_rows(bs.query_stock_industry())
    (VAL / f"_industry_{today[:7]}.json.gz").write_bytes(gzip.compress(json.dumps({"fields": f2, "rows": ind}, ensure_ascii=False).encode()))
    fi = fields.index("code")
    ti = fields.index("type")
    codes = [r[fi] for r in basic if r[ti] == "1"]           # type 1 = stock (listed or delisted)
    done = new = failed = 0
    for code in codes:
        if left() < 120:
            break
        f = VAL / f"{code}.json.gz"
        old = json.loads(gzip.decompress(f.read_bytes())) if f.exists() else {"rows": []}
        start = old["rows"][-1][0] if old["rows"] else "2008-01-01"
        if start >= today:
            done += 1
            continue
        try:
            rows, _ = bs_rows(bs.query_history_k_data_plus(code, VAL_FIELDS, start_date=start, end_date=today, frequency="d", adjustflag="3"))
        except Exception as e:
            failed += 1
            print("val failed", code, type(e).__name__)
            continue
        merged = [r for r in old["rows"] if r[0] < start] + rows
        f.write_bytes(gzip.compress(json.dumps({"fields": VAL_FIELDS.split(","), "rows": merged}).encode()))
        done += 1
        new += len(rows)
    bs.logout()
    return {"stocks_total": len(codes), "stocks_done_this_run": done, "rows_new": new, "failed": failed,
            "industry_rows": len(ind), "files": len(list(VAL.glob("??.*.json.gz")))}


def main():
    parts = os.environ.get("CNX_PART", "fin,val").split(",")
    out = {}
    if "fin" in parts:
        out["fin"] = fin()
        print("[fin]", json.dumps(out["fin"]), flush=True)
    if "val" in parts:
        out["val"] = val()
        print("[val]", json.dumps(out["val"]), flush=True)
    out["minutes"] = round((time.time() - T0) / 60, 1)
    print("[cn_extra]", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
