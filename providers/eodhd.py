"""EODHD source: closing data for the whole market, delayed quotes for a list."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .base import DataUnavailable, Provider

INDICES = [("XJO", "ASX 200", "AXJO"), ("XAO", "所有普通股", "AORD"),
           ("XTL", "ASX 20", "AXTL"), ("XSO", "小盘", "AXSO")]
SECTORS = [("矿业", "AXMJ"), ("金融", "AXFJ"), ("科技", "AXIJ"),
           ("能源", "AXEJ"), ("消费", "AXDJ"), ("医疗", "AXHJ")]
TTL = {"market": 1800, "quotes": 60, "indices": 60, "history": 21600,
       "candles": 600, "intraday": 120, "symbols": 86400}


class EodhdProvider(Provider):
    name = "eodhd"
    latency = "延迟 15–20 分钟"
    capabilities = {"quotes": True, "intraday": False, "sales": False, "book": False}

    def __init__(self):
        self.key = os.environ.get("EODHD_API_KEY", "").strip()
        self.base = os.environ.get("EODHD_BASE", "https://eodhd.com/api").rstrip("/")
        self._cache = {}
        self._lock = threading.Lock()

    def _get(self, path, **params):
        if not self.key:
            raise DataUnavailable("还没有填写 EODHD_API_KEY")
        params.update(api_token=self.key, fmt="json")
        url = f"{self.base}/{path}?{urllib.parse.urlencode(params)}"
        try:
            # 2026-11-17: EODHD started answering 403 to Python's default client header (the browser still worked)
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NeonTerminal/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                why = e.read(160).decode("utf-8", "replace").strip()
            except Exception:
                why = ""
            raise DataUnavailable(f"EODHD 返回 {e.code} {why}".strip()) from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise DataUnavailable("连接 EODHD 失败") from None

    def _cached(self, key, ttl, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (time.time(), value)
        return value

    def symbols(self):
        def load():
            rows = self._get("exchange-symbol-list/AU", type="common_stock")
            return {r["Code"]: r.get("Name", "") for r in rows}
        return self._cached("symbols", TTL["symbols"], load)

    def _bulk(self, day=None):
        args = {"date": day} if day else {}
        return self._get("eod-bulk-last-day/AU", **args) or []

    def market(self):
        return self._cached("market", TTL["market"], self._load_market)

    def _load_market(self):
        latest = self._bulk()
        if not latest:
            raise DataUnavailable("EODHD 没有返回全市场数据")
        asof = max(r["date"] for r in latest)
        previous = []
        d = date.fromisoformat(asof)
        for _ in range(7):
            d -= timedelta(days=1)
            previous = self._bulk(d.isoformat())
            if previous:
                break
        prev = {r["code"]: r["close"] for r in previous}
        names = self.symbols()
        stocks = []
        for r in latest:
            code = r["code"]
            if r["date"] == asof and code in names and prev.get(code) and r.get("close"):
                stocks.append({"code": code, "name": names[code], "last": r["close"],
                               "prev": prev[code], "volume": r.get("volume") or 0})
        return {"asof": asof, "stocks": stocks}

    def _closes(self, symbol, days=70):
        def load():
            start = (date.today() - timedelta(days=days * 2)).isoformat()
            rows = self._get(f"eod/{symbol}", **{"from": start})
            return [r["close"] for r in rows][-days:]
        return self._cached(f"closes:{symbol}", TTL["history"], load)

    def _live(self, symbols):
        out = {}
        for i in range(0, len(symbols), 15):
            batch = symbols[i:i + 15]
            data = self._get(f"real-time/{batch[0]}", s=",".join(batch[1:]))
            for r in data if isinstance(data, list) else [data]:
                out[r["code"]] = r
        return out

    @staticmethod
    def _num(value):
        return value if isinstance(value, (int, float)) else None

    def quotes(self, codes):
        def load():
            sym = {c: (f"{self.INDEX_SYMBOLS[c]}.INDX" if c in self.INDEX_SYMBOLS else f"{c}.AU") for c in codes}
            live = self._live(list(sym.values()))
            result = []
            for c in codes:
                r = live.get(sym[c]) or live.get(sym[c].split(".")[0])
                if not r or self._num(r.get("close")) is None:
                    continue
                result.append({
                    "code": c, "last": r["close"],
                    "prev": self._num(r.get("previousClose")),
                    "open": self._num(r.get("open")), "high": self._num(r.get("high")),
                    "low": self._num(r.get("low")), "volume": self._num(r.get("volume")) or 0,
                    "time": r.get("timestamp"), "spark": self._safe_closes(sym[c], 40),
                })
            return result
        return self._cached("quotes:" + ",".join(codes), TTL["quotes"], load)

    def _safe_closes(self, symbol, days):
        try:
            return self._closes(symbol, 70)[-days:]
        except DataUnavailable:
            return []

    def indices(self):
        return self._cached("indices", TTL["indices"], self._load_indices)

    def _load_indices(self):
        syms = [f"{s}.INDX" for _, _, s in INDICES] + [f"{s}.INDX" for _, s in SECTORS]
        live = self._live(syms)

        def reading(code, label, sym):
            s = f"{sym}.INDX"
            r = live.get(s) or {}
            history = self._safe_closes(s, 70)
            last, prev = self._num(r.get("close")), self._num(r.get("previousClose"))
            if last is None and len(history) >= 2:
                last, prev = history[-1], history[-2]
            if last is None:
                return None
            return {"code": code, "label": label, "last": last, "prev": prev, "history": history}

        main = [reading(c, l, s) for c, l, s in INDICES]
        sectors = [reading(l, l, s) for l, s in SECTORS]
        return {"main": [x for x in main if x], "sectors": [x for x in sectors if x]}

    INDEX_SYMBOLS = {"XJO": "AXJO", "XAO": "AORD", "XTL": "AXTL", "XSO": "AXSO",
                     "XMJ": "AXMJ", "XFJ": "AXFJ", "XIJ": "AXIJ", "XEJ": "AXEJ",
                     "XDJ": "AXDJ", "XHJ": "AXHJ", "XNJ": "AXNJ", "XSJ": "AXSJ", "XTJ": "AXTJ",
                     "XUJ": "AXUJ", "XPJ": "AXPJ", "XEC": "AXEC", "XSR": "AXSR", "XSI": "AXSI"}

    # 行业页 (Frank 2026-11-21: macro -> sector -> small caps). Group, code, Chinese label, GICS sector name for leaders.
    BOARD = [("大盘", "XJO", "ASX 200", None), ("小盘", "XSO", "小盘指数 Small Ords", None),
             ("小盘", "XEC", "新兴公司 Emerging", None), ("小盘", "XSR", "小盘资源", None), ("小盘", "XSI", "小盘工业", None),
             ("行业", "XMJ", "原材料/矿业", "Materials"), ("行业", "XEJ", "能源", "Energy"), ("行业", "XFJ", "金融", "Financials"),
             ("行业", "XHJ", "医疗", "Health Care"), ("行业", "XIJ", "信息技术", "Information Technology"),
             ("行业", "XDJ", "可选消费", "Consumer Discretionary"), ("行业", "XSJ", "必需消费", "Consumer Staples"),
             ("行业", "XNJ", "工业", "Industrials"), ("行业", "XTJ", "通信", "Communication Services"),
             ("行业", "XUJ", "公用事业", "Utilities"), ("行业", "XPJ", "房地产", "Real Estate")]

    def _long(self, symbol):
        def load():
            start = (date.today() - timedelta(days=560)).isoformat()
            return [(r["date"], r.get("adjusted_close") or r["close"]) for r in self._get(f"eod/{symbol}", **{"from": start}) if r.get("close")]
        return self._cached(f"long:{symbol}", TTL["history"], load)

    def _sector_map(self, codes):
        """GICS sector per stock from the ASX company API, cached on this PC (data/sector_map.json, never in git)."""
        from pathlib import Path
        f = Path(__file__).resolve().parent.parent / "data" / "sector_map.json"
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            m = {}
        todo = [c for c in codes if c not in m][:1500]   # first fill ~1 minute, then only new codes
        if todo:
            from concurrent.futures import ThreadPoolExecutor

            def one(c):
                try:
                    req = urllib.request.Request(f"https://asx.api.markitdigital.com/asx-research/1.0/companies/{c.lower()}/header",
                                                 headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return c, (json.load(r).get("data") or {}).get("sector") or ""
                except Exception:
                    return c, None
            with ThreadPoolExecutor(12) as ex:
                for c, sct in ex.map(one, todo):
                    if sct is not None:
                        m[c] = sct
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(m), encoding="utf-8")
            except Exception:
                pass
        return m

    def weekly(self):
        """周报 (Saturday weekly report, 2026-11-21): this calendar week's index and sector moves (Friday close vs the last
        close before Monday), sectors ranked, and the 3 strongest liquid stocks per sector."""
        return self._cached("weekly", 1800, self._load_weekly)

    WEEK_MIN_VALUE = 200_000   # 扣除流动性太差的: last day's turnover (price x volume) at least A$200k ...
    WEEK_MIN_PRICE = 0.05      # ... and price at least 5 cents

    def _load_weekly(self):
        xjo = self._long("AXJO.INDX")
        if len(xjo) < 10:
            raise DataUnavailable("ASX 200 数据不足，周报暂时做不了")
        last = xjo[-1][0]
        monday = date.fromisoformat(last) - timedelta(days=date.fromisoformat(last).weekday())
        base_day = max(d for d, _ in xjo if d < monday.isoformat())
        days_in_week = sum(1 for d, _ in xjo if d > base_day)

        def wk(rows):
            m = dict(rows)
            return round(100 * (rows[-1][1] / m[base_day] - 1), 2) if rows and rows[-1][0] == last and m.get(base_day) else None
        idx = []
        for grp, code, label, gics in self.BOARD:
            try:
                rows = self._long(f"{self.INDEX_SYMBOLS[code]}.INDX")
            except DataUnavailable:
                continue
            w = wk(rows)
            if w is not None:
                idx.append({"group": grp, "code": code, "label": label, "gics": gics, "w": w, "last": round(rows[-1][1], 1)})
        # every stock: last close (market) vs the base day's adjusted close (bulk file; adjusted for splits/consolidations)
        mk = self.market()
        base = {r["code"]: r.get("adjusted_close") or r["close"]
                for r in self._cached(f"bulk:{base_day}", 86400, lambda: self._bulk(base_day)) if r.get("close")}
        raw_base = {r["code"]: r["close"] for r in self._cached(f"bulk:{base_day}", 86400, lambda: self._bulk(base_day)) if r.get("close")}
        stocks = []
        for s in mk["stocks"]:
            b = base.get(s["code"])
            val = s["last"] * (s["volume"] or 0)
            if not b or s["last"] < self.WEEK_MIN_PRICE or val < self.WEEK_MIN_VALUE:
                continue
            w = round(100 * (s["last"] / b - 1), 1)
            rb = raw_base.get(s["code"])
            check = []
            if rb and abs(rb / b - 1) > 0.02:
                check.append("本周有除权/合股调整")
            if abs(w) >= 50:
                check.append("涨跌超 50%：核对合股、配股、收购")
            stocks.append({"code": s["code"], "name": s["name"], "last": s["last"], "w": w, "value_k": round(val / 1000),
                           "check": "；".join(check) or None})
        smap = self._sector_map([x["code"] for x in sorted(stocks, key=lambda x: -x["value_k"])])
        sectors = []
        for r in idx:
            if r["gics"]:
                g = [x for x in stocks if smap.get(x["code"]) == r["gics"]]
                ws = sorted(x["w"] for x in g)
                sectors.append({**r, "n": len(g), "median": ws[len(ws) // 2] if ws else None,
                                "up_pct": round(100 * sum(1 for x in ws if x > 0) / len(ws)) if ws else None,
                                "top": sorted(g, key=lambda x: -x["w"])[:3]})
        sectors.sort(key=lambda x: -x["w"])
        ws = sorted(x["w"] for x in stocks)
        return {"asof": last, "stocks_asof": mk["asof"], "week_from": monday.isoformat(), "base_day": base_day, "days": days_in_week,
                "complete": date.fromisoformat(last).weekday() == 4,
                "index": [r for r in idx if not r["gics"]], "sectors": sectors,
                "breadth": {"n": len(stocks), "up_pct": round(100 * sum(1 for x in ws if x > 0) / len(ws)) if ws else None,
                            "median": ws[len(ws) // 2] if ws else None},
                "filter": f"成交额 ≥ {self.WEEK_MIN_VALUE // 1000}k 澳元、股价 ≥ {self.WEEK_MIN_PRICE} 澳元"}

    def board(self):
        return self._cached("board", 1800, self._load_board)

    def _load_board(self):
        def chg(rows, n):
            return round(100 * (rows[-1][1] / rows[-1 - n][1] - 1), 1) if len(rows) > n and rows[-1 - n][1] else None
        series, out = {}, []
        for grp, code, label, gics in self.BOARD:
            try:
                rows = self._long(f"{self.INDEX_SYMBOLS[code]}.INDX")
            except DataUnavailable:
                rows = []
            if len(rows) < 30:
                continue
            series[code] = rows
            ma = sum(v for _, v in rows[-200:]) / 200 if len(rows) >= 200 else None
            out.append({"group": grp, "code": code, "label": label, "gics": gics, "asof": rows[-1][0], "last": round(rows[-1][1], 1),
                        "d1": chg(rows, 1), "w1": chg(rows, 5), "m1": chg(rows, 21), "m3": chg(rows, 63), "y1": chg(rows, 252),
                        "above200": (rows[-1][1] >= ma) if ma else None, "ma200_gap": round(100 * (rows[-1][1] / ma - 1), 1) if ma else None})
        xj = next((r for r in out if r["code"] == "XJO"), None)
        for r in out:
            r["rs3m"] = round(r["m3"] - xj["m3"], 1) if xj and r["m3"] is not None and xj["m3"] is not None else None
        # every stock: today and ~1 month (bulk end-of-day files: two calls)
        mk = self.market()
        names = {s["code"]: s["name"] for s in mk["stocks"]}
        month = {}
        d = date.fromisoformat(mk["asof"]) - timedelta(days=30)
        for _ in range(6):
            rows = self._cached(f"bulk:{d.isoformat()}", 86400, lambda d=d: self._bulk(d.isoformat()))
            if rows:
                month = {r["code"]: r.get("adjusted_close") or r["close"] for r in rows if r.get("close")}
                break
            d -= timedelta(days=1)
        stocks = []
        for s in mk["stocks"]:
            val = s["last"] * (s["volume"] or 0)
            if s["last"] < 0.01 or val < 20_000:
                continue
            m0 = month.get(s["code"])
            stocks.append({"code": s["code"], "name": s["name"], "last": s["last"], "d1": round(100 * (s["last"] / s["prev"] - 1), 1),
                           "m1": round(100 * (s["last"] / m0 - 1), 1) if m0 else None, "value_k": round(val / 1000)})
        smap = self._sector_map([x["code"] for x in sorted(stocks, key=lambda x: -x["value_k"])])
        leaders = {}
        for r in out:
            if r["gics"]:
                g = [x for x in stocks if smap.get(x["code"]) == r["gics"] and x["m1"] is not None]
                leaders[r["code"]] = sorted(g, key=lambda x: -x["m1"])[:6]
                r["n_stocks"] = len([x for x in stocks if smap.get(x["code"]) == r["gics"]])
                r["breadth_up_m1"] = round(100 * sum(1 for x in g if x["m1"] > 0) / len(g)) if g else None
        xs = next((r for r in out if r["code"] == "XSO"), None)
        m1s = [x["m1"] for x in stocks if x["m1"] is not None]
        mood = {"small_vs_large_3m": round(xs["m3"] - xj["m3"], 1) if xs and xj and xs["m3"] is not None and xj["m3"] is not None else None,
                "small_above200": xs["above200"] if xs else None, "small_ma200_gap": xs["ma200_gap"] if xs else None,
                "up_today_pct": round(100 * sum(1 for x in stocks if x["d1"] > 0) / len(stocks)) if stocks else None,
                "up_month_pct": round(100 * sum(1 for x in m1s if x > 0) / len(m1s)) if m1s else None,
                "median_month_pct": round(sorted(m1s)[len(m1s) // 2], 1) if m1s else None, "n": len(stocks),
                "sector_known": sum(1 for x in stocks if smap.get(x["code"]))}
        return {"asof": mk["asof"], "rows": out, "leaders": leaders, "mood": mood}

    def history(self, code, years=20):
        idx = self.INDEX_SYMBOLS.get(code)
        symbol = f"{idx}.INDX" if idx else f"{code}.AU"
        def load():
            start = date(date.today().year - years, 1, 1).isoformat()
            rows = self._get(f"eod/{symbol}", **{"from": start})
            out = [{"date": r["date"], "close": r.get("adjusted_close") or r.get("close")}
                   for r in rows if (r.get("adjusted_close") or r.get("close"))]
            if len(out) < 300:
                raise DataUnavailable(f"{code} 历史数据不足")
            return out
        return self._cached(f"hist:{symbol}:{years}", TTL["history"], load)

    def candles(self, code, tf):
        idx = self.INDEX_SYMBOLS.get(code)
        symbol = f"{idx}.INDX" if idx else f"{code}.AU"   # 行业页: index codes (XMJ, XSO ...) chart like a stock
        if tf in ("1D", "5D"):
            return self._cached(f"i:{code}:{tf}", TTL["intraday"], lambda: self._intraday(symbol, tf))
        # daily: always the full adjusted history (the chart shows the chosen range and zooms out to everything)
        def load():
            rows = self._get(f"eod/{symbol}", **{"from": "1900-01-01"})   # everything EODHD has
            out = []
            for r in rows:
                if not r.get("close"):
                    continue
                k = (r.get("adjusted_close") or r["close"]) / r["close"]
                out.append({"t": r["date"], "o": r["open"] * k, "h": r["high"] * k, "l": r["low"] * k,
                            "c": r["close"] * k, "v": r.get("volume") or 0})
            return out
        return with_today(self, symbol, self._cached(f"c:{code}:all", TTL["candles"], load), "Australia/Sydney", "AXJO.INDX")

    def _intraday(self, symbol, tf):
        span, interval = (3, "5m") if tf == "1D" else (9, "1h")
        now = int(datetime.now(timezone.utc).timestamp())
        rows = self._get(f"intraday/{symbol}", interval=interval,
                         **{"from": now - span * 86400, "to": now})
        rows = [r for r in rows if r.get("close") is not None]
        if not rows:
            raise DataUnavailable("没有分钟数据")
        if tf == "1D":
            last_day = rows[-1]["datetime"][:10]
            rows = [r for r in rows if r["datetime"][:10] == last_day]
        return [{"t": r["datetime"], "o": r["open"], "h": r["high"], "l": r["low"],
                 "c": r["close"], "v": r.get("volume") or 0} for r in rows]

def _flat(prev, day):
    """A day the exchange was open but this stock did not trade: a flat bar at the last close, volume 0 (Frank 2026-11-17:
    hovering over that day must still show a box with the date and volume 0, so nobody thinks data is missing)."""
    c = prev["c"]
    return {"t": day, "o": c, "h": c, "l": c, "c": c, "v": 0, "nt": 1}


def _day(ts, tz):
    """Local trading date of a unix timestamp. Windows Python has no time-zone database unless tzdata is installed
    (that silently broke today's bar on Frank's PC), so fall back to fixed offsets; trading hours are far from midnight."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(int(ts), ZoneInfo(tz)).date().isoformat()
    except Exception:
        from datetime import timedelta
        u = datetime.fromtimestamp(int(ts), timezone.utc)
        off = 8 if tz == "Asia/Shanghai" else (11 if u.month in (10, 11, 12, 1, 2, 3) else 10)
        return (u + timedelta(hours=off)).date().isoformat()


def with_today(provider, symbol, bars, tz, index=None):
    """EODHD publishes the day's EOD bar hours after the close; until then add today's bar from the (delayed) real-time
    quote, like TradingView's forming bar. Days the exchange traded but this stock did not (no trades, suspended) become
    flat bars with volume 0, using the index's trading days as the calendar. Any problem just returns the bars unchanged."""
    if index is None:
        index = "AXJO.INDX" if symbol.endswith(".AU") else "000001.SHG"
    try:
        cal = provider._cached(f"cal:{index}", 3600, lambda: [r["date"] for r in provider._get(f"eod/{index}", **{"from": "1990-01-01"})])
        if bars and cal:
            have = {b["t"][:10] for b in bars}
            first, last = bars[0]["t"][:10], bars[-1]["t"][:10]
            gaps = [d for d in cal if first < d < last and d not in have]
            if gaps:
                out, j = [], 0
                gaps_set = set(gaps)
                merged = sorted(have | gaps_set)
                by = {b["t"][:10]: b for b in bars}
                for d in merged:
                    out.append(by[d] if d in by else _flat(out[-1], d))
                bars = out
        q = provider._cached(f"rt:{symbol}", 60, lambda: provider._get(f"real-time/{symbol}"))
        o, h, l, c, v = (q.get(k) for k in ("open", "high", "low", "close", "volume"))
        day = _day(q["timestamp"], tz) if q.get("timestamp") else ""
        if all(isinstance(x, (int, float)) and x > 0 for x in (o, h, l, c)) and bars and day > bars[-1]["t"][:10]:
            bars = bars + [{"t": day, "o": o, "h": h, "l": l, "c": c, "v": v if isinstance(v, (int, float)) else 0}]
        # the exchange traded today (the index has a newer day) but this stock did not: flat bar, volume 0
        iq = provider._cached(f"rt:{index}", 60, lambda: provider._get(f"real-time/{index}"))
        iday = _day(iq["timestamp"], tz) if iq.get("timestamp") else ""
        if bars and iday and iday > bars[-1]["t"][:10] and (not cal or iday > cal[-1] or iday in cal):
            for d in [d for d in cal if bars[-1]["t"][:10] < d < iday] + [iday]:
                bars = bars + [_flat(bars[-1], d)]
        return bars
    except Exception:
        return bars
