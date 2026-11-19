"""Commodity mode (?m=cm): gold, oil, gas, metals, farm goods. Runs on Frank's PC, standard library only.

Same contract as the other providers (market / quotes / indices / candles).
  * Daily, with a live (15-20 min delayed) last price: EODHD spot precious metals (FOREX pairs, history from ~1980) and
    US commodity funds (USO = WTI oil, BNO = Brent, UNG = gas, CPER = copper, DBC, DBA, grains, miners...).
  * Monthly: World Bank "Pink Sheet" (official, free) for oil, gas, coal, iron ore, base metals, farm goods, fertiliser,
    history from 1960. Parsed straight from the xlsx (zip + XML) so the PC needs no extra packages.
Codes are upper case (GOLD, WTI, IRONORE...).
"""
import io
import re
import threading
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from providers.base import DataUnavailable

DAILY = [  # code, EODHD symbol, name, unit, group
    ("GOLD", "XAUUSD.FOREX", "黄金", "美元/盎司", "贵金属"), ("SILVER", "XAGUSD.FOREX", "白银", "美元/盎司", "贵金属"),
    ("PLATINUM", "XPTUSD.FOREX", "铂金", "美元/盎司", "贵金属"), ("PALLADIUM", "XPDUSD.FOREX", "钯金", "美元/盎司", "贵金属"),
    ("GOLDAUD", "XAUAUD.FOREX", "黄金（澳元）", "澳元/盎司", "贵金属"),
    ("USO", "USO.US", "WTI 原油基金", "美元/份", "能源"), ("BNO", "BNO.US", "布伦特原油基金", "美元/份", "能源"),
    ("UNG", "UNG.US", "天然气基金", "美元/份", "能源"), ("CPER", "CPER.US", "铜基金", "美元/份", "金属"),
    ("DBC", "DBC.US", "大宗商品综合基金", "美元/份", "综合"), ("DBA", "DBA.US", "农产品综合基金", "美元/份", "农产品"),
    ("WEAT", "WEAT.US", "小麦基金", "美元/份", "农产品"), ("CORN", "CORN.US", "玉米基金", "美元/份", "农产品"),
    ("SOYB", "SOYB.US", "大豆基金", "美元/份", "农产品"), ("URA", "URA.US", "铀矿股基金", "美元/份", "矿业股"),
    ("LIT", "LIT.US", "锂电股基金", "美元/份", "矿业股"), ("GDX", "GDX.US", "金矿股基金", "美元/份", "矿业股"),
    ("COPX", "COPX.US", "铜矿股基金", "美元/份", "矿业股"), ("SLX", "SLX.US", "钢铁股基金", "美元/份", "矿业股"),
    ("REMX", "REMX.US", "稀土股基金", "美元/份", "矿业股"),
]
MONTHLY = [  # code, column title in the Pink Sheet, name, group
    ("BRENT", "Crude oil, Brent", "布伦特原油", "能源"), ("WTI", "Crude oil, WTI", "WTI 原油", "能源"),
    ("COAL", "Coal, Australian", "澳洲动力煤", "能源"), ("LNG", "Liquefied natural gas, Japan", "日本液化天然气", "能源"),
    ("NATGAS", "Natural gas, US", "美国天然气", "能源"), ("IRONORE", "Iron ore, cfr spot", "铁矿石", "金属"),
    ("COPPER", "Copper", "铜", "金属"), ("ALUMINIUM", "Aluminum", "铝", "金属"), ("NICKEL", "Nickel", "镍", "金属"),
    ("ZINC", "Zinc", "锌", "金属"), ("LEAD", "Lead", "铅", "金属"), ("TIN", "Tin", "锡", "金属"),
    ("WHEAT", "Wheat, US HRW", "小麦", "农产品"), ("MAIZE", "Maize", "玉米", "农产品"), ("SOYBEANS", "Soybeans", "大豆", "农产品"),
    ("SUGAR", "Sugar, world", "糖", "农产品"), ("COFFEE", "Coffee, Arabica", "咖啡", "农产品"), ("BEEF", "Beef", "牛肉", "农产品"),
    ("COTTON", "Cotton, A Index", "棉花", "农产品"), ("RICE", "Rice, Thai 5%", "大米", "农产品"),
    ("PALMOIL", "Palm oil", "棕榈油", "农产品"), ("UREA", "Urea", "尿素（化肥）", "农产品"),
]
WB_URLS = [
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx",
    "https://thedocs.worldbank.org/en/doc/18675f1d1639c7a34d463f59263ba0a2-0050012025/related/CMO-Historical-Data-Monthly.xlsx",
]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_pink_sheet(raw):
    """{column title: (unit, [[YYYY-MM-01, value], ...])} from the World Bank xlsx, standard library only."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid = next(s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
               for s in wb.find("m:sheets", NS) if s.get("name") == "Monthly Prices")
    target = next(r.get("Target") for r in rels if r.get("Id") == rid).lstrip("/")
    sheet = ET.fromstring(z.read(target if target.startswith("xl/") else "xl/" + target))
    rows = []
    for row in sheet.find("m:sheetData", NS):
        cells = {}
        for c in row:
            t = c.get("t")
            if t == "inlineStr":
                cells[_col(c.get("r"))] = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                continue
            v = c.find("m:v", NS)
            if v is None:
                continue
            val = shared[int(v.text)] if t == "s" else v.text
            if t not in ("s", "str"):
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    pass
            cells[_col(c.get("r"))] = val
        rows.append(cells)
    hdr = next(i for i, r in enumerate(rows) if any(isinstance(v, str) and v.startswith("Crude oil") for v in r.values()))
    names, units = rows[hdr], rows[hdr + 1]
    out = {}
    for r in rows[hdr + 2:]:
        lab = r.get(0)
        if not isinstance(lab, str) or "M" not in lab:
            continue
        y, m = lab.split("M")
        for j, v in r.items():
            if j and isinstance(v, float) and isinstance(names.get(j), str):
                out.setdefault(names[j].strip(), (str(units.get(j) or "").strip("() "), []))[1].append([f"{y}-{int(m):02d}-01", v])
    return out


class CommodityProvider:
    name = "commodities"
    latency = "金属和基金延迟 15–20 分钟；其他是世界银行月度价"

    def __init__(self):
        from providers.eodhd import EodhdProvider
        self.eod = EodhdProvider()
        self._c = {}
        self._lock = threading.Lock()

    def _cached(self, key, ttl, fn):
        with self._lock:
            hit = self._c.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        v = fn()
        with self._lock:
            self._c[key] = (time.time(), v)
        return v

    def _pink(self):
        def load():
            for url in WB_URLS:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=90) as r:
                        return read_pink_sheet(r.read())
                except Exception:
                    continue
            return {}
        return self._cached("pink", 12 * 3600, load)

    def _monthly(self, code):
        col = next(m[1] for m in MONTHLY if m[0] == code)
        pink = self._pink()
        hit = next((k for k in pink if k.startswith(col)), None)
        return pink[hit] if hit else (None, [])

    def market(self):
        def load():
            stocks = []
            live = self.eod._live([d[1] for d in DAILY])
            for code, sym, name, unit, grp in DAILY:
                r = live.get(sym) or {}
                last, prev = self.eod._num(r.get("close")), self.eod._num(r.get("previousClose"))
                if last:
                    stocks.append({"code": code, "name": name, "last": last, "prev": prev or last, "volume": self.eod._num(r.get("volume")) or 0,
                                   "turnover": 0, "unit": unit, "group": grp, "freq": "日", "src": "EODHD " + sym})
            for code, col, name, grp in MONTHLY:
                unit, data = self._monthly(code)
                if len(data) >= 2:
                    stocks.append({"code": code, "name": name + "（月）", "last": data[-1][1], "prev": data[-2][1], "volume": 0, "turnover": 0,
                                   "unit": unit, "group": grp, "freq": "月", "asof": data[-1][0][:7], "src": "世界银行 " + col})
            return {"asof": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "stocks": stocks}
        return self._cached("market", 60, load)

    def quotes(self, codes):
        m = {s["code"]: s for s in self.market()["stocks"]}
        return [{k: m[c][k] for k in ("code", "last", "prev", "volume", "name", "unit", "freq", "src")} for c in codes if c in m]

    def indices(self):
        m = {s["code"]: s for s in self.market()["stocks"]}
        main = [("GOLD", "黄金"), ("SILVER", "白银"), ("USO", "原油基金"), ("COPPER", "铜（月）"), ("IRONORE", "铁矿石（月）")]
        return {"main": [{"code": c, "label": l, "last": m[c]["last"], "prev": m[c]["prev"]} for c, l in main if c in m], "sectors": []}

    def candles(self, code, tf):
        if tf in ("1D", "5D"):
            raise DataUnavailable("商品先只有日线和月线")
        d = next((x for x in DAILY if x[0] == code), None)
        if d:
            def load():
                rows = self.eod._get(f"eod/{d[1]}", **{"from": "1900-01-01"})
                return [{"t": r["date"], "o": r["open"], "h": r["high"], "l": r["low"], "c": r.get("adjusted_close") or r["close"],
                         "v": r.get("volume") or 0} for r in rows if r.get("close")]
            out = self._cached("c:" + code, 600, load)
        elif any(m[0] == code for m in MONTHLY):
            out = [{"t": t, "o": v, "h": v, "l": v, "c": v, "v": 0} for t, v in self._monthly(code)[1]]
        else:
            raise DataUnavailable("没有这个品种")
        if not out:
            raise DataUnavailable("暂时读不到数据")
        return out

    def codes(self):
        return [d[0] for d in DAILY] + [m[0] for m in MONTHLY]
