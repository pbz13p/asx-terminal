"""China A-shares for the terminal (EODHD, same key): market list, delayed quotes, daily candles, indices, a small
per-stock summary, and the latest announcements from cninfo (Chinese, official). Codes are 6 digits; 6xxxxx is
Shanghai (SHG), the rest Shenzhen (SHE)."""
import json
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .base import DataUnavailable
from .eodhd import EodhdProvider

INDICES = [("000001", "上证指数", "000001.SHG"), ("000300", "沪深 300", "000300.SHG"),
           ("399001", "深证成指", "399001.SHE"), ("399006", "创业板指", "399006.SHE")]


def sym(code):
    return f"{code}.{'SHG' if code.startswith(('6', '9')) else 'SHE'}"


def board(code):
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("688", "689")):
        return "科创板"
    return "主板"


class CnProvider(EodhdProvider):
    name = "eodhd-cn"

    def cn_names(self):
        def load():
            names = {}
            try:
                req = urllib.request.Request("http://www.cninfo.com.cn/new/data/szse_stock.json", headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    for s in json.loads(r.read()).get("stockList", []):
                        names[s["code"]] = {"zh": s.get("zwjc"), "org": s.get("orgId")}
            except Exception:
                pass
            for ex in ("SHG", "SHE"):
                try:
                    for r in self._get(f"exchange-symbol-list/{ex}"):
                        if r.get("Type") == "Common Stock":
                            names.setdefault(r["Code"], {})["en"] = r.get("Name")
                except DataUnavailable:
                    pass
            return names
        return self._cached("cn:names", 86400, load)

    def name_of(self, code):
        n = self.cn_names().get(code, {})
        return n.get("zh") or n.get("en") or ""

    def market(self):
        def load():
            rows, asof = [], None
            for ex in ("SHG", "SHE"):
                latest = self._get(f"eod-bulk-last-day/{ex}") or []
                rows += latest
            if not rows:
                raise DataUnavailable("EODHD 没有返回 A 股数据")
            asof = max(r["date"] for r in rows)
            prev = {}
            d = date.fromisoformat(asof)
            for _ in range(7):
                d -= timedelta(days=1)
                p = []
                for ex in ("SHG", "SHE"):
                    p += self._get(f"eod-bulk-last-day/{ex}", date=d.isoformat()) or []
                if p:
                    prev = {r["code"]: r["close"] for r in p}
                    break
            names = self.cn_names()
            stocks = [{"code": r["code"], "name": (names.get(r["code"]) or {}).get("zh") or (names.get(r["code"]) or {}).get("en") or "",
                       "last": r["close"], "prev": prev.get(r["code"]), "volume": r.get("volume") or 0, "board": board(r["code"])}
                      for r in rows if r["date"] == asof and r["code"] in names and r.get("close") and prev.get(r["code"])]
            return {"asof": asof, "stocks": stocks}
        return self._cached("cn:market", 1800, load)

    def quotes(self, codes):
        def load():
            live = self._live([sym(c) for c in codes])
            out = []
            for c in codes:
                r = live.get(sym(c))
                if not r or self._num(r.get("close")) is None:
                    continue
                out.append({"code": c, "last": r["close"], "prev": self._num(r.get("previousClose")), "open": self._num(r.get("open")),
                            "high": self._num(r.get("high")), "low": self._num(r.get("low")), "volume": self._num(r.get("volume")) or 0,
                            "time": r.get("timestamp"), "name": self.name_of(c)})
            return out
        return self._cached("cn:q:" + ",".join(codes), 60, load)

    def indices(self):
        def load():
            main = []
            for code, label, s in INDICES:
                try:
                    rows = self._get(f"eod/{s}", **{"from": (date.today() - timedelta(days=14)).isoformat()})
                    if len(rows) >= 2:
                        main.append({"code": code, "label": label, "last": rows[-1]["close"], "prev": rows[-2]["close"]})
                except DataUnavailable:
                    pass
            return {"main": main, "sectors": []}
        return self._cached("cn:idx", 300, load)

    def candles(self, code, tf):
        if tf in ("1D", "5D"):
            raise DataUnavailable("A 股暂时只有日线")
        days = {"1M": 22, "6M": 126, "1Y": 250}[tf]

        def load():
            rows = self._get(f"eod/{sym(code)}", **{"from": (date.today() - timedelta(days=int(days * 1.6) + 30)).isoformat()})
            out = []
            for r in rows:
                k = (r.get("adjusted_close") or r["close"]) / r["close"] if r.get("close") else 1
                out.append({"t": r["date"], "o": r["open"] * k, "h": r["high"] * k, "l": r["low"] * k, "c": r["close"] * k, "v": r.get("volume") or 0})
            return out[-days:]
        return self._cached(f"cn:c:{code}:{tf}", 600, load)

    def insight(self, code):
        def load():
            rows = self._get(f"eod/{sym(code)}", **{"from": (date.today() - timedelta(days=420)).isoformat()})
            px = [r.get("adjusted_close") or r["close"] for r in rows if r.get("close")]
            if len(px) < 30:
                raise DataUnavailable("历史数据不足")
            last = px[-1]
            chg = lambda n: 100 * (last / px[-1 - n] - 1) if len(px) > n else None
            ma = lambda n: sum(px[-n:]) / n if len(px) >= n else None
            gains = [max(px[i] - px[i - 1], 0) for i in range(len(px) - 14, len(px))]
            losses = [max(px[i - 1] - px[i], 0) for i in range(len(px) - 14, len(px))]
            rsi = 100 - 100 / (1 + sum(gains) / sum(losses)) if sum(losses) else 100
            import math
            r = [math.log(px[i] / px[i - 1]) for i in range(len(px) - 20, len(px))]
            m = sum(r) / len(r)
            rv20 = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1) * 242) * 100
            hi = max(px[-250:])
            S = []
            ma200 = ma(200)
            if ma200 and last < ma200:
                S.append({"kind": "info", "title": "在 200 日线下方", "text": "A 股回测（第 72 轮）里，单独的均线位置对 A 股预测力很弱，只作参考。"})
            if chg(5) is not None and chg(5) > 15:
                S.append({"kind": "down", "title": "近 5 天涨太多", "text": "第 72 轮：A 股近 5 日涨幅最大的一组，之后 1 个月平均少赚 1.4%（t -7.7）。不要追。"})
            if rv20 > 60:
                S.append({"kind": "down", "title": "波动很大", "text": "第 72 轮：A 股波动越大，之后表现越差（模型最看重的因素之一）。"})
            return {"code": code, "name": self.name_of(code), "sector": board(code), "last": last, "asof": rows[-1]["date"],
                    "chg": {"1天": chg(1), "5天": chg(5), "20天": chg(20), "60天": chg(60), "1年": chg(242)},
                    "dist_hi": 100 * (last / hi - 1), "ma50": ma(50), "ma200": ma200, "rsi": rsi, "rv20": rv20, "rv_pct": None, "signals": S}
        return self._cached(f"cn:ins:{code}", 1800, load)

    def announcements(self, code):
        def load():
            org = (self.cn_names().get(code) or {}).get("org") or (("gssh0" if code.startswith("6") else "gssz0") + code)
            body = urllib.parse.urlencode({"pageNum": 1, "pageSize": 30, "column": "sse" if code.startswith("6") else "szse",
                                           "tabName": "fulltext", "stock": f"{code},{org}", "isHLtitle": "true",
                                           "seDate": f"{(date.today() - timedelta(days=730)).isoformat()}~{date.today().isoformat()}"}).encode()
            req = urllib.request.Request("http://www.cninfo.com.cn/new/hisAnnouncement/query", data=body, headers={"Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.loads(r.read())
            except Exception:
                raise DataUnavailable("巨潮资讯暂时连不上") from None
            out = []
            for a in d.get("announcements") or []:
                t = a.get("announcementTitle") or ""
                out.append({"date": time.strftime("%Y-%m-%d", time.gmtime(a["announcementTime"] / 1000 + 8 * 3600)),
                            "headline": t.replace("<em>", "").replace("</em>", ""),
                            "sensitive": any(k in t for k in ("业绩预告", "增持", "减持", "回购", "重大", "立案", "重组", "中标", "收购", "停牌", "风险提示")),
                            "ids": a.get("announcementId"), "pdf": "http://static.cninfo.com.cn/" + (a.get("adjunctUrl") or "")})
            return out
        return self._cached(f"cn:ann:{code}", 1800, load)
