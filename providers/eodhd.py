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
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise DataUnavailable(f"EODHD 返回 {e.code}") from None
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
            live = self._live([f"{c}.AU" for c in codes])
            result = []
            for c in codes:
                r = live.get(f"{c}.AU")
                if not r or self._num(r.get("close")) is None:
                    continue
                result.append({
                    "code": c, "last": r["close"],
                    "prev": self._num(r.get("previousClose")),
                    "open": self._num(r.get("open")), "high": self._num(r.get("high")),
                    "low": self._num(r.get("low")), "volume": self._num(r.get("volume")) or 0,
                    "time": r.get("timestamp"), "spark": self._safe_closes(f"{c}.AU", 40),
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
                     "XDJ": "AXDJ", "XHJ": "AXHJ"}

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
        symbol = f"{code}.AU"
        if tf in ("1D", "5D"):
            return self._cached(f"i:{code}:{tf}", TTL["intraday"], lambda: self._intraday(symbol, tf))
        days = {"1M": 22, "6M": 126, "1Y": 250}[tf]
        def load():
            start = (date.today() - timedelta(days=int(days * 1.6))).isoformat()
            rows = self._get(f"eod/{symbol}", **{"from": start})
            return [{"t": r["date"], "o": r["open"], "h": r["high"], "l": r["low"],
                     "c": r["close"], "v": r.get("volume") or 0} for r in rows][-days:]
        return self._cached(f"c:{code}:{tf}", TTL["candles"], load)

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
