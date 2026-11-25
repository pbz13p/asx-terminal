"""US stocks and ETFs for the terminal (Frank 2026-11-25: "开一个美股板块，把美股加进去，所有的数据").
EODHD (already paid, same key): the whole NYSE / NASDAQ / NYSE American / NYSE Arca / Cboe list with the last close,
delayed quotes, full adjusted daily history plus 5-minute / hourly bars, the main indices and the 11 S&P sector ETFs.
Filings come from SEC EDGAR (official, free; SEC asks for a contact in the User-Agent: set SEC_CONTACT in .env).
Live prices come from 盈透 TWS on Frank's PC (free US feed, Cboe One + IEX). Class shares use a dash (BRK-B)."""
import json
import math
import os
import urllib.request
from datetime import date, timedelta

from .base import DataUnavailable
from .eodhd import EodhdProvider, with_today

EXCHANGES = {"NYSE", "NASDAQ", "NYSE ARCA", "NYSE MKT", "AMEX", "BATS", "CBOE"}
TYPES = {"Common Stock", "ETF"}
# index code -> (EODHD symbol, Chinese label)
INDEX = {"SPX": ("GSPC.INDX", "标普 500"), "IXIC": ("IXIC.INDX", "纳斯达克综合"), "NDX": ("NDX.INDX", "纳斯达克 100"),
         "DJI": ("DJI.INDX", "道琼斯"), "RUT": ("RUT.INDX", "罗素 2000 小盘"), "VIX": ("VIX.INDX", "VIX 恐慌指数")}
SECTOR_ETF = [("XLK", "科技"), ("XLF", "金融"), ("XLE", "能源"), ("XLV", "医疗"), ("XLY", "可选消费"), ("XLP", "必需消费"),
              ("XLI", "工业"), ("XLB", "原材料"), ("XLU", "公用事业"), ("XLRE", "房地产"), ("XLC", "通信")]
FORMS = {"8-K": "重大事项（8-K）", "10-K": "年报（10-K）", "10-Q": "季报（10-Q）", "4": "董事 / 高管买卖股票（表 4）",
         "3": "新任董事 / 高管首次持股（表 3）", "SC 13D": "大股东持股 5% 以上、有意影响公司（13D）",
         "SC 13G": "大股东持股 5% 以上（13G）", "SC 13D/A": "大股东持股变动（13D/A）", "SC 13G/A": "大股东持股变动（13G/A）",
         "S-1": "上市 / 发新股（S-1）", "S-3": "增发新股登记（S-3）", "424B4": "发行定价说明书", "424B5": "增发说明书",
         "DEF 14A": "股东大会通知（DEF 14A）", "6-K": "海外公司公告（6-K）", "20-F": "海外公司年报（20-F）",
         "144": "内部人拟卖出股票（144）", "S-8": "员工股权计划登记（S-8）"}
SENSITIVE = {"8-K", "10-K", "10-Q", "SC 13D", "SC 13D/A", "S-1", "S-3", "424B4", "424B5", "6-K", "20-F", "4", "144"}


def sym(code):
    return INDEX[code][0] if code in INDEX else f"{code}.US"


def _sec_headers():
    return {"User-Agent": "NeonTerminal personal research " + (os.environ.get("SEC_CONTACT") or "github.com/pbz13p"),
            "Accept": "application/json"}


class UsProvider(EodhdProvider):
    name = "eodhd-us"

    def names(self):
        def load():
            out = {}
            for r in self._get("exchange-symbol-list/US"):
                if r.get("Type") in TYPES and (r.get("Exchange") or "").upper() in EXCHANGES and r.get("Code"):
                    out[r["Code"]] = {"name": r.get("Name") or "", "ex": r.get("Exchange"), "etf": r.get("Type") == "ETF"}
            if not out:
                raise DataUnavailable("EODHD 没有返回美股代码表")
            return out
        return self._cached("us:names", 86400, load)

    def market(self):
        def load():
            latest = self._get("eod-bulk-last-day/US") or []
            if not latest:
                raise DataUnavailable("EODHD 没有返回美股数据")
            asof = max(r["date"] for r in latest)
            prev, d = {}, date.fromisoformat(asof)
            for _ in range(7):
                d -= timedelta(days=1)
                p = self._get("eod-bulk-last-day/US", date=d.isoformat()) or []
                if p:
                    prev = {r["code"]: r["close"] for r in p}
                    break
            nm = self.names()
            stocks = [{"code": r["code"], "name": nm[r["code"]]["name"], "last": r["close"], "prev": prev.get(r["code"]),
                       "volume": r.get("volume") or 0, "board": "ETF" if nm[r["code"]]["etf"] else nm[r["code"]]["ex"]}
                      for r in latest if r["date"] == asof and r["code"] in nm and r.get("close") and prev.get(r["code"])]
            return {"asof": asof, "stocks": stocks}
        return self._cached("us:market", 1800, load)

    def name_of(self, code):
        if code in INDEX:
            return INDEX[code][1]
        try:
            return (self.names().get(code) or {}).get("name") or ""
        except DataUnavailable:
            return ""

    def quotes(self, codes):
        def load():
            live = self._live([sym(c) for c in codes])
            out = []
            for c in codes:
                r = live.get(sym(c)) or live.get(sym(c).split(".")[0])
                if not r or self._num(r.get("close")) is None:
                    continue
                out.append({"code": c, "last": r["close"], "prev": self._num(r.get("previousClose")), "open": self._num(r.get("open")),
                            "high": self._num(r.get("high")), "low": self._num(r.get("low")), "volume": self._num(r.get("volume")) or 0,
                            "time": r.get("timestamp"), "name": self.name_of(c)})
            return out
        return self._cached("us:q:" + ",".join(codes), 60, load)

    def indices(self):
        def load():
            syms = [v[0] for v in INDEX.values()] + [f"{e}.US" for e, _ in SECTOR_ETF]
            live = self._live(syms)
            pick = lambda s: live.get(s) or live.get(s.split(".")[0])
            main, sectors = [], []
            for code, (s, label) in INDEX.items():
                r = pick(s)
                if r and self._num(r.get("close")) is not None:
                    main.append({"code": code, "label": label, "last": r["close"], "prev": self._num(r.get("previousClose"))})
            for e, label in SECTOR_ETF:
                r = pick(f"{e}.US")
                if r and self._num(r.get("close")) is not None:
                    sectors.append({"code": e, "label": label, "last": r["close"], "prev": self._num(r.get("previousClose"))})
            return {"main": main, "sectors": sectors}
        return self._cached("us:idx", 120, load)

    def candles(self, code, tf):
        s = sym(code)
        if tf in ("1D", "5D"):
            return self._cached(f"us:i:{code}:{tf}", 120, lambda: self._intraday(s, tf))

        def load():
            rows = self._get(f"eod/{s}", **{"from": "1900-01-01"})
            out = []
            for r in rows:
                if not r.get("close"):
                    continue
                k = (r.get("adjusted_close") or r["close"]) / r["close"]
                out.append({"t": r["date"], "o": r["open"] * k, "h": r["high"] * k, "l": r["low"] * k, "c": r["close"] * k,
                            "v": r.get("volume") or 0})
            if not out:
                raise DataUnavailable("没有这只美股的数据")
            return out
        return with_today(self, s, self._cached(f"us:c:{code}:all", 600, load), "America/New_York", "GSPC.INDX")

    def insight(self, code):
        def load():
            rows = self._get(f"eod/{sym(code)}", **{"from": (date.today() - timedelta(days=420)).isoformat()})
            px = [r.get("adjusted_close") or r["close"] for r in rows if r.get("close")]
            if len(px) < 30:
                raise DataUnavailable("历史数据不足")
            last = px[-1]
            chg = lambda n: 100 * (last / px[-1 - n] - 1) if len(px) > n else None
            ma = lambda n: sum(px[-n:]) / n if len(px) >= n else None
            g = [max(px[i] - px[i - 1], 0) for i in range(len(px) - 14, len(px))]
            ls = [max(px[i - 1] - px[i], 0) for i in range(len(px) - 14, len(px))]
            rsi = 100 - 100 / (1 + sum(g) / sum(ls)) if sum(ls) else 100
            r = [math.log(px[i] / px[i - 1]) for i in range(len(px) - 20, len(px))]
            m = sum(r) / len(r)
            rv20 = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1) * 252) * 100
            hi = max(px[-252:])
            try:
                info = self.names().get(code) or {}
            except DataUnavailable:
                info = {}
            S, ma200 = [], ma(200)
            if ma200 and last < ma200:
                S.append({"kind": "info", "title": "在 200 日线下方", "text": "长期趋势向下。美股我们还没做自己的回测，只作参考。"})
            if last <= hi * 0.5:
                S.append({"kind": "info", "title": "离一年高点跌了一半以上", "text": f"一年最高 {hi:.2f}，现在 {last:.2f}。"})
            if rv20 > 80:
                S.append({"kind": "down", "title": "波动很大", "text": f"近 20 天年化波动 {rv20:.0f}%，仓位要小。"})
            sector = "指数" if code in INDEX else ("ETF" if info.get("etf") else (info.get("ex") or "美股"))
            return {"code": code, "name": self.name_of(code), "sector": sector, "last": last, "asof": rows[-1]["date"],
                    "chg": {"1天": chg(1), "5天": chg(5), "20天": chg(20), "60天": chg(60), "1年": chg(252)},
                    "dist_hi": 100 * (last / hi - 1), "ma50": ma(50), "ma200": ma200, "rsi": rsi, "rv20": rv20, "rv_pct": None,
                    "signals": S}
        return self._cached(f"us:ins:{code}", 1800, load)

    def _sec_get(self, url):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_sec_headers()), timeout=30) as r:
                return json.load(r)
        except Exception:
            raise DataUnavailable("SEC 网站暂时连不上") from None

    def _cik(self, code):
        m = self._cached("us:cik", 86400, lambda: {v["ticker"].upper().replace(".", "-"): v["cik_str"]
                                                    for v in self._sec_get("https://www.sec.gov/files/company_tickers.json").values()})
        return m.get(code)

    def announcements(self, code):
        """Recent SEC filings (official). Headline = what the form is, in Chinese; the link opens the filing itself."""
        def load():
            if code in INDEX:
                return []
            cik = self._cik(code)
            if not cik:
                return []            # ETFs and some foreign listings have no company filings under the ticker
            d = self._sec_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
            r = (d.get("filings") or {}).get("recent") or {}
            out, cutoff = [], (date.today() - timedelta(days=730)).isoformat()
            for form, day, acc, doc, desc in zip(r.get("form", []), r.get("filingDate", []), r.get("accessionNumber", []),
                                                 r.get("primaryDocument", []), r.get("primaryDocDescription", [])):
                if day < cutoff:
                    break
                label = FORMS.get(form, form)
                if desc and form in ("8-K", "6-K") and desc.upper() not in ("8-K", "6-K"):
                    label += " · " + desc[:60]
                out.append({"date": day, "headline": label, "sensitive": form in SENSITIVE, "ids": acc,
                            "pdf": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}"})
                if len(out) >= 60:
                    break
            return out
        try:
            return self._cached(f"us:ann:{code}", 1800, load)
        except DataUnavailable:
            # SEC refuses requests without a contact (SEC_CONTACT in .env); meanwhile show EODHD's news for the ticker
            return self._cached(f"us:news:{code}", 1800, lambda: self._news(code))

    def _news(self, code):
        rows = self._get("news", s=sym(code), limit=40) or []
        return [{"date": (r.get("date") or "")[:10], "headline": r.get("title") or "", "sensitive": False,
                 "ids": r.get("link") or "", "pdf": r.get("link") or ""} for r in rows if r.get("title")]
