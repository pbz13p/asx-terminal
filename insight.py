"""Direction panel for one stock: the stock's own state + macro state + the research rules that apply.

Everything here comes from the research rounds in web/report.html. It describes what history says,
not what will happen. Free sources only: EODHD (already paid), RBA statistical tables, FRED,
ASX company directory, Forex Factory weekly calendar.
"""
import csv
import io
import json
import math
import time
import urllib.request
from datetime import date, datetime, timedelta

UA = {"User-Agent": "Mozilla/5.0 (ASX Desk)"}
_cache = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        if hit:
            return hit[1]
        return None
    _cache[key] = (time.time(), val)
    return val


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ sources
def rba_au2():
    text = get("https://www.rba.gov.au/statistics/tables/csv/f2-data.csv")
    rows = list(csv.reader(io.StringIO(text)))
    ids = next(r for r in rows if r and r[0].strip().lower() == "series id")
    col = ids.index("FCMYGBAG2D")
    col10 = ids.index("FCMYGBAG10D")
    out = []
    for r in rows[rows.index(ids) + 1:]:
        try:
            d = datetime.strptime(r[0].strip(), "%d-%b-%Y").date().isoformat()
            out.append((d, float(r[col]), float(r[col10]) if r[col10].strip() else None))
        except (ValueError, IndexError):
            continue
    return out


def fred_last(series):
    text = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}")
    rows = [r for r in csv.reader(io.StringIO(text))][1:]
    rows = [(r[0], float(r[1])) for r in rows if len(r) > 1 and r[1] not in (".", "")]
    return rows[-1] if rows else None


def sector_map():
    for u in ("https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file",
              "https://www.asx.com.au/asx/research/ASXListedCompanies.csv"):
        try:
            lines = get(u, 60).splitlines()
            start = next(i for i, l in enumerate(lines) if "ASX code" in l or "ASX Code" in l)
            out = {}
            for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
                code = (row.get("ASX code") or row.get("ASX Code") or "").strip()
                name = (row.get("Company name") or row.get("Company Name") or "").strip()
                ind = (row.get("GICS industry group") or row.get("GICs industry group") or "").strip()
                if code:
                    out[code] = (name, ind)
            if out:
                return out
        except Exception:
            continue
    raise RuntimeError("no directory")


def asic_latest():
    for back in range(3, 14):
        d = date.today() - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                f"https://download.asic.gov.au/short-selling/RR{d.strftime('%Y%m%d')}-001-SSDailyAggShortPos.csv", headers=UA), timeout=30).read()
        except Exception:
            continue
        text = None
        for enc in ("utf-16", "utf-8-sig", "latin-1"):
            try:
                text = raw.decode(enc)
                if "Product" in text[:400]:
                    break
            except UnicodeDecodeError:
                continue
        delim = "\t" if text[:400].count("\t") > text[:400].count(",") else ","
        out = {}
        for row in csv.reader(io.StringIO(text), delimiter=delim):
            try:
                out[row[1].strip()] = float(row[4].strip().replace("%", ""))
            except (ValueError, IndexError):
                continue
        if len(out) > 200:
            return {"date": d.isoformat(), "data": out}
    raise RuntimeError("ASIC unavailable")


def ff_week():
    rows = json.loads(get("https://nfs.faireconomy.media/ff_calendar_thisweek.json"))
    out = []
    for r in rows:
        if r.get("country") in ("AUD", "USD") and r.get("impact") == "High":
            out.append({"when": r.get("date", "")[:16].replace("T", " "), "country": r["country"],
                        "title": r.get("title"), "forecast": r.get("forecast"), "previous": r.get("previous"),
                        "actual": r.get("actual")})
    return out


# ------------------------------------------------------------------ research constants (rounds 4-10)
GROUP_TO_SECTOR = {
    "Materials": "矿业", "Energy": "能源", "Banks": "金融", "Financial Services": "金融", "Insurance": "金融",
    "Diversified Financials": "金融", "Equity Real Estate Investment Trusts (REITs)": "地产信托",
    "Real Estate Management & Development": "地产信托", "Real Estate": "地产信托",
    "Pharmaceuticals, Biotechnology & Life Sciences": "医疗", "Health Care Equipment & Services": "医疗",
    "Software & Services": "科技", "Technology Hardware & Equipment": "科技",
    "Semiconductors & Semiconductor Equipment": "科技", "Consumer Services": "可选消费", "Retailing": "可选消费",
    "Consumer Discretionary Distribution & Retail": "可选消费", "Automobiles & Components": "可选消费",
    "Consumer Durables & Apparel": "可选消费", "Food, Beverage & Tobacco": "必需消费",
    "Consumer Staples Distribution & Retail": "必需消费", "Food & Staples Retailing": "必需消费",
    "Household & Personal Products": "必需消费", "Capital Goods": "工业",
    "Commercial & Professional Services": "工业", "Transportation": "工业", "Utilities": "公用事业",
    "Telecommunication Services": "电信", "Media & Entertainment": "电信",
}
# 1-month excess return vs ASX 200 by macro state (round 9)
STATE_EXCESS = {
    "cut": {"科技": 4.54, "地产信托": 1.19, "可选消费": 1.11, "工业": 0.37, "金融": 0.26, "医疗": 0.13, "能源": -0.34,
            "电信": -0.44, "矿业": -0.46, "公用事业": -1.77, "必需消费": -1.84},
    "hike": {"公用事业": 2.41, "能源": 1.52, "矿业": 1.27, "工业": 0.28, "金融": 0.11, "地产信托": -0.06,
             "必需消费": -0.20, "可选消费": -0.43, "电信": -0.55, "医疗": -1.89, "科技": -2.68},
    "panic": {"科技": 3.12, "矿业": 1.08, "能源": 0.83, "可选消费": 0.23, "金融": 0.06, "公用事业": -0.17,
              "工业": -0.38, "地产信托": -0.43, "电信": -0.54, "医疗": -0.89, "必需消费": -0.99},
    "normal": {"矿业": 0.64, "工业": 0.22, "公用事业": 0.08, "金融": 0.05, "可选消费": 0.02, "地产信托": -0.06,
               "必需消费": -0.12, "医疗": -0.17, "能源": -0.27, "科技": -0.37, "电信": -0.40},
    "calm": {"医疗": 1.13, "科技": 0.71, "可选消费": 0.51, "必需消费": 0.49, "工业": 0.13, "公用事业": 0.11,
             "地产信托": 0.03, "金融": -0.08, "矿业": -0.18, "能源": -0.32, "电信": -0.60},
}
RATE_BETA = {"能源": 0.53, "金融": 0.09, "矿业": 0.06, "科技": -0.09, "医疗": -0.11, "地产信托": -0.42,
             "可选消费": -0.11, "必需消费": -0.03, "工业": -0.08, "公用事业": -0.07, "电信": -0.11}
MONTH = {1: (1.09, 67), 2: (0.06, 58), 3: (-1.77, 50), 4: (2.07, 75), 5: (0.47, 67), 6: (-0.15, 67),
         7: (2.83, 92), 8: (-0.54, 50), 9: (-2.04, 25), 10: (0.34, 50), 11: (2.05, 67), 12: (0.98, 64)}
RBA_DATES = ["2026-02-03", "2026-03-17", "2026-05-05", "2026-06-16", "2026-08-11", "2026-09-29", "2026-11-03", "2026-12-08"]
KNOWN_EVENTS = [("2026-12-08", "澳联储 利率决议（下午 2:30）"), ("2026-12-10", "美联储决议后的澳股交易日")]


THEMES = {
    "黄金": ("GLD.US", ["NST", "EVN", "RRL", "PRU", "RMS", "WGX", "GOR", "DEG", "CMM", "RSG", "SBM", "WAF", "PDI", "BGL", "GMD", "VAU", "SLR"]),
    "石油天然气": ("USO.US", ["WDS", "STO", "BPT", "KAR", "STX", "CVN", "SXY"]),
    "铜": ("CPER.US", ["SFR", "29M", "AIS", "HCH", "C6C", "AZY", "S32", "SND", "CYM"]),
    "锂": ("LIT.US", ["PLS", "MIN", "IGO", "LTR", "CXO", "SYA", "LKE", "VUL", "GL1", "INR"]),
    "铀": ("URA.US", ["PDN", "BOE", "DYL", "LOT", "BMN", "PEN", "AGE", "DEV", "NXG"]),
}
THEME_TEXT = {  # round 17/23: overnight behaviour (catch-up only robust for gold)
    "黄金": ("+3.5%", "金价隔夜涨跌开盘就反映完，别开盘追。金价 20 天涨超 5% 时，金矿股之后 20 天平均跑赢大盘约 5%。"),
    "石油天然气": ("+2.0%", "油价隔夜涨跌基本在开盘反映完。"),
    "铜": ("+1.5%", "铜价隔夜大涨后，铜矿股开盘后平均还会多走约 0.6%。"),
    "锂": ("+2.2%", "锂价隔夜涨跌开盘就反映完；锂股更像“强者恒强”，领先的往往继续领先。"),
    "铀": ("+4.2%", "铀价隔夜涨跌开盘后还会顺势走约 0.7–0.8%（反应慢）。"),
}


def theme_state(theme, provider):
    etf, members = THEMES[theme]
    start = (date.today() - timedelta(days=3 * 365)).isoformat()
    ser = lambda sym: {r["date"]: r.get("adjusted_close") or r["close"] for r in provider._get(f"eod/{sym}", **{"from": start})}
    e, aud = ser(etf), ser("AUDUSD.FOREX")
    xj = {r["date"]: float(r["close"]) for r in provider.history("XJO", 3)}
    mem = {}
    for m in members:
        try:
            mem[m] = {r["date"]: float(r["close"]) for r in provider.history(m, 3)}
        except Exception:
            pass
    days = sorted(xj)
    ek, ak = sorted(e), sorted(aud)
    import bisect

    def us(d):
        j, k = bisect.bisect_left(ek, d) - 1, bisect.bisect_left(ak, d) - 1
        return e[ek[j]] / aud[ak[k]] if j >= 0 and k >= 0 else None
    b, u = [], []
    for i in range(1, len(days)):
        d, p = days[i], days[i - 1]
        rs = [s[d] / s[p] - 1 for s in mem.values() if d in s and p in s and s[p] > 0]
        u1, u0 = us(d), us(p)
        if len(rs) < 3 or not u1 or not u0:
            continue
        b.append(sum(rs) / len(rs) - (xj[d] / xj[p] - 1))
        u.append(u1 / u0 - 1)
    n = len(b)
    mb, mu = sum(b) / n, sum(u) / n
    bt = sum((x - mu) * (y - mb) for x, y in zip(u, b)) / sum((x - mu) ** 2 for x in u)
    cum = lambda a, i, j: math.prod(1 + x for x in a[i:j]) - 1
    res = [cum(b, i - 20, i) - bt * cum(u, i - 20, i) for i in range(20, n + 1)]
    sd = math.sqrt(sum(x * x for x in res) / len(res))
    return {"z": res[-1] / sd, "commodity_20d": 100 * cum(u, n - 20, n), "basket_20d": 100 * cum(b, n - 20, n), "etf": etf}


def season(d):
    md = d[5:]
    return ("02-05" <= md <= "03-05") or ("08-05" <= md <= "09-05")


def sig(kind, title, text):
    return {"kind": kind, "title": title, "text": text}


# ------------------------------------------------------------------ main
def insight(code, provider):
    hist = provider.history(code, 3)
    px = [float(r["close"]) for r in hist]
    ds = [r["date"] for r in hist]
    last = px[-1]

    def chg(n):
        return 100 * (last / px[-1 - n] - 1) if len(px) > n else None
    win = px[-252:]
    hi, lo = max(win), min(win)
    ma = lambda n: sum(px[-n:]) / n if len(px) >= n else None
    ma50, ma200 = ma(50), ma(200)
    rets = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    rv = lambda w: math.sqrt(sum(x * x for x in w) / len(w) * 252) * 100
    rv20 = rv(rets[-20:])
    rv_hist = [rv(rets[i - 20:i]) for i in range(20, len(rets) + 1, 5)]
    rv_pct = 100 * sum(1 for v in rv_hist if v <= rv20) / len(rv_hist)
    gains = [max(0, px[i] - px[i - 1]) for i in range(len(px) - 14, len(px))]
    losses = [max(0, px[i - 1] - px[i]) for i in range(len(px) - 14, len(px))]
    rsi = 100 - 100 / (1 + (sum(gains) / sum(losses))) if sum(losses) else 100.0

    directory = cached("dir", 86400, sector_map) or {}
    name, group = directory.get(code, ("", ""))
    sector = GROUP_TO_SECTOR.get(group)

    au2 = cached("au2", 6 * 3600, rba_au2) or []
    us10 = cached("us10", 6 * 3600, lambda: fred_last("DGS10"))
    vix = cached("vix", 3600, lambda: provider._get("eod/VIX.INDX", **{"from": (date.today() - timedelta(days=10)).isoformat()})[-1]["close"])
    week = cached("ff", 3600, ff_week) or []

    macro = {"vix": vix, "us10": us10[1] if us10 else None}
    au2_chg = None
    if len(au2) > 64:
        macro["au2"], macro["au10"], macro["au_asof"] = au2[-1][1], au2[-1][2], au2[-1][0]
        au2_chg = au2[-1][1] - au2[-64][1]
        macro["au2_3m"] = au2_chg
    hawk = None
    if au2:
        yd = [x[0] for x in au2]
        for rd in RBA_DATES:
            if rd in yd:
                k = yd.index(rd)
                if k >= 1 and au2[k][1] - au2[k - 1][1] >= .06 and len(yd) - 1 - k < 5:
                    hawk = (rd, 100 * (au2[k][1] - au2[k - 1][1]))
    rate_state = None if au2_chg is None else "cut" if au2_chg < -.5 else "hike" if au2_chg > .5 else "flat"
    vix_state = None if vix is None else "panic" if vix >= 25 else "calm" if vix < 15 else "normal"
    macro["rate_state"] = {"cut": "市场押降息", "hike": "市场押加息", "flat": "利率预期平稳", None: "未知"}[rate_state]
    macro["vix_state"] = {"panic": "恐慌", "calm": "平静", "normal": "正常", None: "未知"}[vix_state]

    S = []
    today = date.today().isoformat()
    # ---- stock signals
    d1 = chg(1)
    if d1 is not None and d1 >= 10:
        if season(ds[-1]):
            S.append(sig("info", "业绩季跳涨", f"上个交易日涨 {d1:.1f}%，正值业绩季。业绩跳涨后追买在 2022 年前有效，之后对大中盘股已经没有优势（第 33 轮），别指望追涨。"))
        else:
            S.append(sig("down", "非业绩大涨，别追", f"上个交易日涨 {d1:.1f}%。没有业绩支撑的大涨，第二天开盘后通常回吐（涨 10–20% 后盘中平均 −0.8%）。"))
    if d1 is not None and d1 <= -5:
        if season(ds[-1]):
            S.append(sig("warn", "业绩季大跌，先看原因", f"上个交易日跌 {d1:.1f}%，正值业绩季。如果是业绩公布后放量跳空低开，历史上之后一个月平均再跌约 3%，别急着抄底；如果不是业绩原因，则偏向反弹。"))
        else:
            S.append(sig("up", "非业绩大跌，偏反弹", f"上个交易日跌 {d1:.1f}%。没有业绩原因的大跌，历史上之后一个月偏向反弹。"))
    dist_hi = 100 * (last / hi - 1)
    # breakout (round 21): new 52-week high in the last 5 days after a 15%+ pullback within 60 days
    brk = None
    for t in range(len(px) - 5, len(px)):
        if t >= 253 and px[t] > max(px[t - 252:t]) and min(px[t - 60:t]) <= max(px[t - 252:t]) * .85:
            brk = ds[t]
    if brk:
        S.append(sig("up", "52 周新高突破", f"{brk} 创 52 周新高，而且 3 个月内曾比高点低 15% 以上（真正的突破）。如果当天放量（≥平时 2 倍），历史上之后 60 天平均跑赢一般股票约 3.6%（很稳定），但只买突破股做成组合时跑输 ASX 200，适合用来挑时机，不宜单独作为买入理由。一半的突破会失败。"))
    if dist_hi >= -5 and not brk:
        S.append(sig("info", "靠近一年高点", f"离 52 周高点只差 {abs(dist_hi):.1f}%。“靠近高点”的股票比一般小股票略强（约 +0.5%/月），但在大盘股里做成组合并没有跑赢 ASX 200（第 29 轮），只作参考。"))
    elif dist_hi <= -40:
        S.append(sig("info", "远离一年高点", f"比 52 周高点低 {abs(dist_hi):.0f}%。这个信号 2022 年前后表现相反，不作为方向依据。"))
    r5 = chg(5)
    if r5 is not None and r5 >= 15:
        S.append(sig("info", "5 天涨太多", f"近 5 天涨 {r5:.1f}%。接下来 1–5 天容易回吐（短线别追），但看 1 个月并没有劣势。"))
    elif r5 is not None and r5 <= -15:
        S.append(sig("info", "5 天跌太多", f"近 5 天跌 {abs(r5):.1f}%。短线有反弹倾向，但 2022 年后看 1 个月不稳定，先确认不是业绩问题。"))
    if ma50 and ma200:
        if last > ma50 > ma200:
            S.append(sig("info", "趋势向上", "股价在 50 日线和 200 日线上方，且 50 日线高于 200 日线。比一般小股票略强，但不足以跑赢 ASX 200，只作参考。"))
        elif last < ma50 < ma200:
            S.append(sig("down", "趋势向下", "股价在 50 日线和 200 日线下方，且 50 日线低于 200 日线。过去一年最弱、离高点最远的大盘股，2022 年后平均每年亏损（第 29 轮），别因为“跌多了便宜”就买。"))
    if rv_pct >= 80:
        S.append(sig("warn", "波动偏大", f"近 20 天年化波动 {rv20:.0f}%，处于自身历史第 {rv_pct:.0f} 百分位。仓位宜小一点，止损放宽。"))
    # ---- dividends (round 15)
    divinfo = None
    try:
        divs = cached(f"div:{code}", 6 * 3600, lambda: provider._get(f"div/{code}.AU", **{"from": (date.today() - timedelta(days=800)).isoformat()})) or []
        past = [x for x in divs if x.get("date", "") <= today]
        fut = [x for x in divs if x.get("date", "") > today]
        if past or fut:
            lastd = past[-1] if past else None
            yr = sum(float(x.get("unadjustedValue") or x.get("value") or 0) for x in past
                     if x.get("date", "") > (date.today() - timedelta(days=365)).isoformat())
            divinfo = {"last_ex": lastd and lastd.get("date"), "last_amt": lastd and (lastd.get("unadjustedValue") or lastd.get("value")),
                       "franking": (fut or past)[-1 if not fut else 0].get("franking"), "yield": 100 * yr / last if last else None,
                       "next_ex": fut[0].get("date") if fut else None, "next_amt": fut[0].get("unadjustedValue") if fut else None}
            if fut:
                days_to = (date.fromisoformat(fut[0]["date"]) - date.today()).days
                if 0 < days_to <= 28:
                    S.append(sig("info", f"{days_to} 天后除息", f"除息日 {fut[0]['date']}。除息前 10 天平均只跑赢大盘约 0.4%，扣成本后为 0，2022 年后更弱，不值得专门为此买卖；除息日股价通常下跌约等于现金股息。"))
    except Exception:
        pass
    # ---- commodity theme (round 17)
    theme = next((t for t, (_, ms) in THEMES.items() if code in ms), None)
    tinfo = None
    if theme:
        tinfo = cached(f"theme:{theme}", 6 * 3600, lambda: theme_state(theme, provider))
        if tinfo:
            tinfo["theme"] = theme
            z = tinfo["z"]
            catch, note = THEME_TEXT[theme]
            if z < -1 and theme == "黄金":
                S.append(sig("up", "金矿股落后于金价", f"金矿股篮子近 20 天相对大盘 {tinfo['basket_20d']:+.1f}%，金价 {tinfo['commodity_20d']:+.1f}%，明显落后（z={z:.1f}）。2015 年以来 28 次独立出现，之后 20 天平均补涨约 5%（中位数 3.7%，68% 的情况为正）。"))
            elif z < -1:
                S.append(sig("info", f"{theme}股落后于商品", f"{theme}股近 20 天落后于自己的商品（z={z:.1f}）。按独立事件检验，{theme}股的“补涨”在统计上不显著，只作参考。"))
            elif z > 1:
                S.append(sig("info", f"{theme}股跑在商品前面", f"{theme}股篮子近 20 天跑赢自己的商品（z={z:.1f}），之后补涨优势消失，2022 年后略偏弱。"))
            if theme == "黄金" and tinfo["commodity_20d"] > 5:
                S.append(sig("up", "金价 1 个月大涨", f"金价（澳元）近 20 天 {tinfo['commodity_20d']:+.1f}%。历史上之后 20 天金矿股平均跑赢大盘约 5%。"))
            S.append(sig("info", f"{theme}隔夜规律", note))
    # ---- short selling (round 19)
    short = None
    asic = cached("asic", 12 * 3600, asic_latest)
    if asic and code in asic["data"]:
        short = {"pct": asic["data"][code], "date": asic["date"]}
        if short["pct"] >= 10:
            S.append(sig("down", "空头很高", f"有 {short['pct']:.1f}% 的股份被卖空（ASIC {asic['date']}）。空头 ≥10% 的股票 2022 年后平均每月跑输一般股票约 0.7%，也更容易出现剧烈的轧空反弹。"))
        elif short["pct"] >= 6:
            S.append(sig("info", "空头偏高", f"有 {short['pct']:.1f}% 的股份被卖空（ASIC {asic['date']}）。"))
    # ---- market (ASX 200) signals
    try:
        xh = provider.history("XJO", 3)
        xc = [float(r["close"]) for r in xh]
        # halt then reopen lower (round 28): sessions missing from the stock's history in the last 15 days
        xd = [r["date"] for r in xh][-16:]
        have = set(ds[-40:])
        for t in range(1, len(xd)):
            if xd[t] in have and xd[t - 1] not in have:
                k0 = t - 1
                while k0 >= 0 and xd[k0] not in have:
                    k0 -= 1
                if k0 >= 0 and 1 <= t - 1 - k0 <= 5:
                    a_, b_ = ds.index(xd[k0]), ds.index(xd[t])
                    move = 100 * (px[b_] / px[a_] - 1)
                    if move <= -5:
                        S.append(sig("down", "停牌复牌后大跌（多为配股融资）", f"{xd[t]} 停牌后复牌，较停牌前 {move:.1f}%。这类股票（多为折价配股）复牌后 5 天平均再跑输一般股票约 3%（t≈−5，两段时间都成立），别急着接。"))
    

        downs = 0
        while downs < len(xc) - 1 and xc[-1 - downs] < xc[-2 - downs]:
            downs += 1
        macro["xjo_down_days"] = downs
        if downs >= 3:
            S.append(sig("up", f"大盘连跌 {downs} 天", "ASX 200 连跌 3 天后买入、持有 5 天：历史上平均 +0.4%，两段时间都成立；做成策略回撤只有一直持有的一半。"))
    except Exception:
        pass
    # ---- macro signals
    if hawk:
        S.append(sig("down", "澳联储鹰派意外后一周", f"{hawk[0]} 决议当天澳 2 年收益率 +{hawk[1]:.0f}bp（比预期鹰派）。历史上之后一周 ASX 200 平均 −0.7%（p≈0.006），所有板块偏弱，REITs 最弱。ETF 回测：这一周把仓位减半，长期每年多约 1%。"))
    if vix_state == "panic":
        S.append(sig("up", "VIX 恐慌", f"VIX {vix:.1f} ≥ 25。历史上之后 1 个月 ASX 200 平均 +1.7%、3 个月 +5%，上涨概率约 76%。"))
    if rate_state == "cut":
        S.append(sig("up", "市场押降息", f"澳 2 年收益率 3 个月内 {au2_chg:+.2f}%。历史上之后 1 个月 ASX 200 平均 +2.9%，7 段时期全部上涨。"))
    elif rate_state == "hike":
        S.append(sig("down", "市场押加息", f"澳 2 年收益率 3 个月内 {au2_chg:+.2f}%。历史上之后 3 个月 ASX 200 偏弱（平均 −0.9%）。"))
    if sector:
        state = vix_state if vix_state == "panic" else rate_state if rate_state in ("cut", "hike") else vix_state
        if state in STATE_EXCESS:
            ex = STATE_EXCESS[state][sector] if sector in STATE_EXCESS[state] else None
            label = {"cut": "市场押降息", "hike": "市场押加息", "panic": "VIX 恐慌", "normal": "VIX 正常", "calm": "VIX 平静"}[state]
            if ex is not None and abs(ex) >= .3:
                S.append(sig("info", f"{sector}板块 · {label}",
                             f"在“{label}”环境下，{sector}板块历史上 1 个月平均{'跑赢' if ex > 0 else '跑输'}大盘 {abs(ex):.1f}%。但样本外检验（第 27 轮）按环境换板块并不赚钱，只作背景参考。"))
        b = RATE_BETA.get(sector)
        if b is not None and abs(b) >= .3:
            S.append(sig("info", "利率敏感", f"{sector}板块对利率很敏感：澳 2 年收益率每涨 0.1%，相对大盘约 {b:+.2f}%。"))
    m = int(today[5:7])
    mm, up = MONTH[m]
    if abs(mm) >= 1.5:
        S.append(sig("up" if mm > 0 else "down", f"{m} 月季节性", f"ASX 200 在 {m} 月平均 {mm:+.1f}%，{up}% 的年份上涨。{'（部分是除息造成）' if m in (3, 9) else ''}"))
    if today[5:] >= "12-15" or today[5:] <= "01-08":
        S.append(sig("up", "圣诞行情窗口", "12/15 到 1 月第 5 个交易日，12 年里 11 年上涨，但 2022 年后变弱。"))
    # ---- events
    events = [{"when": d, "title": t} for d, t in KNOWN_EVENTS if today <= d <= (date.today() + timedelta(days=40)).isoformat()]
    score = sum(1 for s in S if s["kind"] == "up") - sum(1 for s in S if s["kind"] == "down")
    return {"code": code, "name": name, "group": group, "sector": sector or "未分类",
            "last": last, "asof": ds[-1],
            "chg": {"1天": d1, "5天": r5, "20天": chg(20), "60天": chg(60), "1年": chg(252)},
            "hi": hi, "lo": lo, "dist_hi": dist_hi, "ma50": ma50, "ma200": ma200, "rsi": rsi,
            "rv20": rv20, "rv_pct": rv_pct, "macro": macro, "signals": S, "score": score,
            "events": events, "week": week[:12], "div": divinfo, "theme": tinfo, "short": short,
            "spark": [round(x, 4) for x in px[-120:]]}
