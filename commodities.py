"""Commodity prices for the terminal and for research (Frank 2026-11-20: gold, oil, futures, raw materials).

Sources (one primary source per series, CLAUDE.md "不重复下载"):
  * EODHD (already paid): spot precious metals as FOREX pairs, back to ~1980; US commodity ETFs/ETCs, which trade
    every day and stand in for futures (our plan has no futures exchange: probed 2026-11-20, COMM = 404).
  * FRED (US Federal Reserve, free, public): daily spot oil and gas (EIA), monthly IMF world prices for metals,
    energy and farm goods (iron ore, copper, nickel, coal, uranium, wheat...).
What it is for: rounds 17/23/39 (commodity -> ASX resource stocks), CND-type oil and gas names, round 80/82 features
(commodity trend behind a ten-bagger theme), and the terminal's commodity board.
Output: data/commodities/<id>.json = [[date, close], ...] (ETFs and metals: [date, open, high, low, close, volume])
plus data/commodities/index.json (names, units, source, first/last date). Run daily on Actions; cached + backed up.
"""
import csv
import io
import json
import time
import urllib.request
from pathlib import Path

import study

OUT = Path("data/commodities")
EOD = [  # id, EODHD symbol, Chinese name, unit / note
    ("gold", "XAUUSD.FOREX", "黄金现货", "美元/盎司"), ("silver", "XAGUSD.FOREX", "白银现货", "美元/盎司"),
    ("platinum", "XPTUSD.FOREX", "铂金现货", "美元/盎司"), ("palladium", "XPDUSD.FOREX", "钯金现货", "美元/盎司"),
    ("gold_aud", "XAUAUD.FOREX", "黄金（澳元）", "澳元/盎司"),
    ("etf_oil_wti", "USO.US", "原油 WTI（USO 基金）", "美元/份"), ("etf_oil_brent", "BNO.US", "布伦特原油（BNO 基金）", "美元/份"),
    ("etf_natgas", "UNG.US", "天然气（UNG 基金）", "美元/份"), ("etf_copper", "CPER.US", "铜（CPER 基金）", "美元/份"),
    ("etf_commodities", "DBC.US", "大宗商品综合（DBC 基金）", "美元/份"), ("etf_agri", "DBA.US", "农产品综合（DBA 基金）", "美元/份"),
    ("etf_wheat", "WEAT.US", "小麦（WEAT 基金）", "美元/份"), ("etf_corn", "CORN.US", "玉米（CORN 基金）", "美元/份"),
    ("etf_soy", "SOYB.US", "大豆（SOYB 基金）", "美元/份"), ("etf_uranium", "URA.US", "铀矿股（URA 基金）", "美元/份"),
    ("etf_lithium", "LIT.US", "锂电股（LIT 基金）", "美元/份"), ("etf_gold_miners", "GDX.US", "金矿股（GDX 基金）", "美元/份"),
    ("etf_silver_miners", "SIL.US", "银矿股（SIL 基金）", "美元/份"), ("etf_copper_miners", "COPX.US", "铜矿股（COPX 基金）", "美元/份"),
    ("etf_steel", "SLX.US", "钢铁股（SLX 基金）", "美元/份"), ("etf_rare_earth", "REMX.US", "稀土股（REMX 基金）", "美元/份"),
    ("etf_battery_metals", "BATT.US", "电池金属（BATT 基金）", "美元/份"),
]
FRED = [  # id, FRED series, name, unit, frequency
    ("wti", "DCOILWTICO", "原油 WTI 现货", "美元/桶", "日"), ("brent", "DCOILBRENTEU", "布伦特原油现货", "美元/桶", "日"),
    ("henry_hub", "DHHNGSP", "天然气 Henry Hub", "美元/百万英热", "日"), ("heating_oil", "DHOILNYH", "取暖油（纽约港）", "美元/加仑", "日"),
    ("gasoline", "DGASNYH", "汽油（纽约港）", "美元/加仑", "日"),
    ("iron_ore", "PIORECRUSDM", "铁矿石", "美元/吨", "月"), ("copper", "PCOPPUSDM", "铜", "美元/吨", "月"),
    ("aluminium", "PALUMUSDM", "铝", "美元/吨", "月"), ("nickel", "PNICKUSDM", "镍", "美元/吨", "月"),
    ("zinc", "PZINCUSDM", "锌", "美元/吨", "月"), ("lead", "PLEADUSDM", "铅", "美元/吨", "月"), ("tin", "PTINUSDM", "锡", "美元/吨", "月"),
    ("uranium", "PURANUSDM", "铀", "美元/磅", "月"), ("coal_aus", "PCOALAUUSDM", "澳洲动力煤", "美元/吨", "月"),
    ("lng_asia", "PNGASJPUSDM", "亚洲液化天然气", "美元/百万英热", "月"), ("wheat", "PWHEAMTUSDM", "小麦", "美元/吨", "月"),
    ("soybeans", "PSOYBUSDM", "大豆", "美元/吨", "月"), ("corn", "PMAIZMTUSDM", "玉米", "美元/吨", "月"),
    ("sugar", "PSUGAISAUSDM", "糖", "美分/磅", "月"), ("coffee", "PCOFFOTMUSDM", "咖啡", "美分/磅", "月"),
    ("beef", "PBEEFUSDM", "牛肉", "美分/磅", "月"), ("wool", "PWOOLCUSDM", "羊毛", "美分/公斤", "月"),
]


def fred(series):
    req = urllib.request.Request(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode()
            return [[row[0], float(row[1])] for row in list(csv.reader(io.StringIO(text)))[1:] if len(row) >= 2 and row[1] not in (".", "")]
        except Exception as e:
            err = e
            time.sleep(5 * (attempt + 1))
    print(f"[commodities] {series} failed: {type(err).__name__}", flush=True)
    return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index, ok = [], 0
    for cid, sym, name, unit in EOD:
        rows = study.api(f"eod/{sym}", **{"from": "1900-01-01"}) or []
        data = [[r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("adjusted_close") or r.get("close"), r.get("volume")] for r in rows if r.get("close")]
        if data:
            (OUT / f"{cid}.json").write_text(json.dumps(data))
            ok += 1
        index.append({"id": cid, "name": name, "unit": unit, "source": "EODHD " + sym, "freq": "日",
                      "first": data[0][0] if data else None, "last": data[-1][0] if data else None, "n": len(data)})
    for cid, ser, name, unit, freq in FRED:
        data = fred(ser)
        if data:
            (OUT / f"{cid}.json").write_text(json.dumps(data))
            ok += 1
        elif (OUT / f"{cid}.json").exists():
            data = json.loads((OUT / f"{cid}.json").read_text())   # keep yesterday's copy if FRED is down
        index.append({"id": cid, "name": name, "unit": unit, "source": "FRED " + ser, "freq": freq,
                      "first": data[0][0] if data else None, "last": data[-1][0] if data else None, "n": len(data or [])})
    (OUT / "index.json").write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "series": index},
                                               ensure_ascii=False, indent=1))
    print(f"[commodities] {ok}/{len(EOD) + len(FRED)} series updated", flush=True)
    for x in index:
        print(f"[commodities] {x['id']}: {x['n']} rows {x['first']}..{x['last']}", flush=True)   # dates only, no prices


if __name__ == "__main__":
    main()
