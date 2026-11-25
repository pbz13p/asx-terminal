"""Crypto mode (?m=cx): Binance USDT-margined perpetuals, real time, public endpoints (no key, no login).

Runs on Frank's PC (Australia), which reaches fapi.binance.com directly. Same contract as the stock providers:
market / quotes / indices / candles, plus the funding rate (every 8 hours, the cost of holding a perpetual).
"""
import json
import time
import urllib.request
from datetime import datetime, timezone

from providers.base import DataUnavailable

BASE = "https://fapi.binance.com"
TOP = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE")


class BinanceProvider:
    name = "binance"
    latency = "real-time"

    def __init__(self):
        self._c = {}

    def _get(self, path, **params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{BASE}{path}" + (f"?{q}" if q else "")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as r:
                return json.load(r)
        except Exception as e:
            raise DataUnavailable(f"Binance 连不上：{type(e).__name__}")

    def _cached(self, key, ttl, fn):
        hit = self._c.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self._c[key] = (time.time(), val)
        return val

    def market(self):
        def load():
            # crypto only: Binance also lists stock / gold / index perpetuals (NVDA, XAU...) -- underlyingType COIN keeps coins
            coins = self._cached("info", 3600, lambda: {x["symbol"] for x in self._get("/fapi/v1/exchangeInfo")["symbols"]
                                                         if x.get("contractType") == "PERPETUAL" and x.get("quoteAsset") == "USDT"
                                                         and x.get("status") == "TRADING" and x.get("underlyingType", "COIN") == "COIN"})
            tick = self._get("/fapi/v1/ticker/24hr")
            prem = {p["symbol"]: p for p in self._get("/fapi/v1/premiumIndex")}
            stocks = []
            for t in tick:
                s = t["symbol"]
                if s not in coins:
                    continue
                last = float(t["lastPrice"])
                if last <= 0 or float(t.get("count") or 0) == 0:
                    continue
                f = prem.get(s, {})
                fr = float(f.get("lastFundingRate") or 0)
                stocks.append({"code": s[:-4], "name": s, "last": last, "prev": last - float(t["priceChange"]),
                               "volume": float(t["volume"]), "turnover": float(t["quoteVolume"]),
                               "funding": fr, "funding_year_pct": round(fr * 3 * 365 * 100, 1),
                               "next_funding": f.get("nextFundingTime")})
            asof = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            return {"asof": asof, "stocks": stocks}
        return self._cached("market", 20, load)

    def quotes(self, codes):
        m = {s["code"]: s for s in self.market()["stocks"]}
        return [{"code": c, "last": m[c]["last"], "prev": m[c]["prev"], "volume": m[c]["volume"], "name": m[c]["name"],
                 "funding": m[c]["funding"], "funding_year_pct": m[c]["funding_year_pct"]} for c in codes if c in m]

    def indices(self):
        m = {s["code"]: s for s in self.market()["stocks"]}
        return {"main": [{"code": c, "label": c + " 永续", "last": m[c]["last"], "prev": m[c]["prev"]} for c in TOP if c in m],
                "sectors": []}

    INTRA = ("1m", "5m", "15m", "1h", "4h")
    SECS = {"5s": 5, "15s": 15, "30s": 30}

    def _sec_bars(self, code, sec, minutes=20):
        """Seconds bars (Frank 2026-11-25, Binance's futures API has no bars under 1 minute): built from the last
        `minutes` of aggregated trades, paged 1,000 at a time; the page then keeps them going from the live trade stream."""
        end = int(time.time() * 1000)
        start, bars = end - minutes * 60000, {}
        for _ in range(60):
            rows = self._get("/fapi/v1/aggTrades", symbol=code + "USDT", startTime=start, endTime=end, limit=1000)
            if not rows:
                break
            for r in rows:
                t = r["T"] // 1000 // sec * sec
                p, q = float(r["p"]), float(r["q"])
                b = bars.get(t)
                if b is None:
                    bars[t] = [p, p, p, p, q]
                else:
                    b[1] = max(b[1], p); b[2] = min(b[2], p); b[3] = p; b[4] += q
            if len(rows) < 1000:
                break
            start = rows[-1]["T"] + 1
        if not bars:
            raise DataUnavailable("最近没有成交")
        return [{"t": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "o": b[0], "h": b[1], "l": b[2],
                 "c": b[3], "v": round(b[4], 6)} for t, b in sorted(bars.items())]

    def candles(self, code, tf):
        if tf in self.SECS:
            return self._cached(f"c:{code}:{tf}", 3, lambda: self._sec_bars(code, self.SECS[tf]))
        if tf in ("1D", "5D"):
            tf = "5m" if tf == "1D" else "15m"
        if tf in self.INTRA:   # Frank 2026-11-25: minute / hour bars like Binance; last 1,500 bars, times in UTC
            def load_i():
                rows = self._get("/fapi/v1/klines", symbol=code + "USDT", interval=tf, limit=1500)
                if not rows:
                    raise DataUnavailable("没有这个合约")
                return [{"t": datetime.fromtimestamp(r[0] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                         "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]
            return self._cached(f"c:{code}:{tf}", 5, load_i)

        def load():
            out, start = [], 1500000000000
            while True:
                rows = self._get("/fapi/v1/klines", symbol=code + "USDT", interval="1d", limit=1500, startTime=start)
                if not rows:
                    break
                for r in rows:
                    out.append({"t": datetime.fromtimestamp(r[0] / 1000, timezone.utc).strftime("%Y-%m-%d"),
                                "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
                if len(rows) < 1500:
                    break
                start = rows[-1][0] + 1
            if not out:
                raise DataUnavailable("没有这个合约")
            return out
        return self._cached("c:" + code, 60, load)
