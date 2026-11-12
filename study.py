"""Whole-market short-horizon study (1, 2 and 5 trading days) for the ASX.

Downloads daily history for every ASX common stock, delisted ones included,
plus the ASX 200 and the S&P 500, then tests a set of short-term patterns
after trading costs, separately for 2015-2021 and 2022 onwards.

Run: double-click study.bat (or `py study.py`). Results go to data/.
"""
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "eod"
START = "2014-01-01"
SPLIT = "2022-01-01"
HORIZONS = (1, 2, 5)
COMMISSION = 0.001  # 0.1% round trip assumed broker cost


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()


load_env()
KEY = os.environ.get("EODHD_API_KEY", "").strip()
BASE = os.environ.get("EODHD_BASE", "https://eodhd.com/api").rstrip("/")


# ---------------------------------------------------------------- download
def api(path, **params):
    params.update(api_token=KEY, fmt="json")
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            if e.code == 404:
                return []
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (attempt + 1))
    return []


def universe():
    cur = api("exchange-symbol-list/AU")
    old = api("exchange-symbol-list/AU", delisted=1)
    seen, out = set(), []
    for rows, delisted in ((cur, False), (old, True)):
        for r in rows or []:
            if r.get("Type") != "Common Stock":
                continue
            code = r.get("Code")
            if code and code not in seen:
                seen.add(code)
                out.append((code, delisted))
    return out


def fetch(symbol):
    f = CACHE / (symbol.replace("/", "_") + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < 20 * 3600:
        return
    rows = api(f"eod/{symbol}", **{"from": START})
    keep = [[r["date"], r.get("open"), r.get("high"), r.get("low"), r.get("close"),
             r.get("adjusted_close"), r.get("volume")] for r in rows or []]
    f.write_text(json.dumps(keep), encoding="utf-8")


def download(symbols):
    CACHE.mkdir(parents=True, exist_ok=True)
    done, lock, t0 = [0], threading.Lock(), time.time()

    def job(s):
        try:
            fetch(s)
        except Exception as e:  # keep going; report at the end
            print(f"\n  skipped {s}: {type(e).__name__}")
        with lock:
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == len(symbols):
                el = time.time() - t0
                left = el / done[0] * (len(symbols) - done[0])
                print(f"\r  下载 {done[0]}/{len(symbols)}  预计还要 {left / 60:.0f} 分钟   ", end="", flush=True)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(job, symbols))
    print()


def load(symbol):
    f = CACHE / (symbol.replace("/", "_") + ".json")
    if not f.exists():
        return []
    out = []
    for d, o, h, l, c, a, v in json.loads(f.read_text(encoding="utf-8")):
        if not c or c <= 0 or not a or a <= 0:
            continue
        k = a / c
        out.append((d, (o or c) * k, (h or c) * k, (l or c) * k, a, v or 0, c))
    return out


# ---------------------------------------------------------------- stats
class Agg:
    """Collects event returns, clustered by date for honest t-statistics."""

    def __init__(self):
        self.by_date = defaultdict(lambda: [0.0, 0.0, 0, 0])

    def add(self, d, gross, net):
        a = self.by_date[d]
        a[0] += gross
        a[1] += net
        a[2] += 1
        a[3] += 1 if net > 0 else 0

    def summary(self, lo=None, hi=None):
        items = [(d, a) for d, a in self.by_date.items() if (lo is None or d >= lo) and (hi is None or d < hi)]
        n = sum(a[2] for _, a in items)
        if n == 0:
            return None
        g = sum(a[0] for _, a in items) / n
        net = sum(a[1] for _, a in items) / n
        win = sum(a[3] for _, a in items) / n
        daily = [a[1] / a[2] for _, a in items]
        t = 0.0
        if len(daily) > 2:
            m = sum(daily) / len(daily)
            sd = math.sqrt(sum((x - m) ** 2 for x in daily) / (len(daily) - 1))
            t = m / (sd / math.sqrt(len(daily))) if sd > 0 else 0.0
        return {"n": n, "days": len(items), "gross": g * 100, "net": net * 100,
                "win": win * 100, "t": t}


STUDIES = defaultdict(Agg)


def liq_bucket(value):
    if value < 1e4:
        return "L0 <1万"
    if value < 1e5:
        return "L1 1-10万"
    if value < 1e6:
        return "L2 10-100万"
    return "L3 >100万"


SPREAD = {"L0 <1万": .03, "L1 1-10万": .015, "L2 10-100万": .006, "L3 >100万": .002}


def cost(price, liq):
    tick = .001 if price < .1 else .005 if price < 2 else .01
    return COMMISSION + max(SPREAD[liq], tick / price)


def median(a):
    b = sorted(a)
    n = len(b)
    return 0 if n == 0 else (b[n // 2] if n % 2 else (b[n // 2 - 1] + b[n // 2]) / 2)


def move_bucket(r):
    for lo, hi, name in ((-9, -.2, "≤-20%"), (-.2, -.1, "-20%~-10%"), (-.1, -.05, "-10%~-5%"),
                         (.05, .1, "+5%~+10%"), (.1, .2, "+10%~+20%"), (.2, 9, "≥+20%")):
        if lo <= r < hi:
            return name
    return None


def record(study, bucket, rows, i, liq, entry="open"):
    """Signal at close of day i. Enter next day's open (default) or day i close."""
    if i + 1 >= len(rows):
        return
    if entry == "open":
        px = rows[i + 1][1]
        if rows[i + 1][5] <= 0:
            return
    else:
        px = rows[i][4]
    if px <= 0:
        return
    c = cost(rows[i][6], liq)
    for h in HORIZONS:
        j = i + h
        if j >= len(rows):
            break
        g = rows[j][4] / px - 1
        if abs(g) > 3:  # bad print or unadjusted consolidation
            return
        STUDIES[(study, bucket, liq, h)].add(rows[i][0], g, g - c)
        if liq != "L0 <1万":
            STUDIES[(study, bucket, "全部", h)].add(rows[i][0], g, g - c)


WEEKLY = defaultdict(list)  # date -> [(past5, next5, liq)]


def scan_stock(rows):
    n = len(rows)
    if n < 80:
        return 0
    vals = [r[6] * r[5] for r in rows]
    events = 0
    hq = deque()
    for k in range(59):
        while hq and rows[hq[-1]][2] <= rows[k][2]:
            hq.pop()
        hq.append(k)
    for i in range(60, n - 1):
        while hq and rows[hq[-1]][2] <= rows[i - 1][2]:
            hq.pop()
        hq.append(i - 1)
        d = rows[i][0]
        if d < "2015-01-01":
            continue
        if rows[i][5] <= 0 or rows[i - 1][4] <= 0:
            continue
        med_val = median(vals[i - 20:i])
        if med_val <= 0:
            continue
        liq = liq_bucket(med_val)
        r1 = rows[i][4] / rows[i - 1][4] - 1
        if abs(r1) > 3:
            continue
        b = move_bucket(r1)
        if b:
            record("S1 单日大涨大跌后", b, rows, i, liq)
            events += 1
        med_vol = median([r[5] for r in rows[i - 20:i]])
        if med_vol > 0 and rows[i][5] >= 5 * med_vol:
            record("S3 放量 5 倍", "放量上涨" if r1 > 0 else "放量下跌" if r1 < 0 else "放量平盘", rows, i, liq)
        gap = rows[i][1] / rows[i - 1][4] - 1
        if gap >= .05 or gap <= -.05:
            tag = "高开≥5%" if gap > 0 else "低开≥5%"
            c = cost(rows[i][6], liq)
            g = rows[i][4] / rows[i][1] - 1
            if abs(g) < 3:
                STUDIES[("S4 跳空当天(开盘买到收盘)", tag, liq, 0)].add(d, g, g - c)
                if liq != "L0 <1万":
                    STUDIES[("S4 跳空当天(开盘买到收盘)", tag, "全部", 0)].add(d, g, g - c)
            record("S4 跳空之后", tag, rows, i, liq)
        if i >= 250:
            while hq and hq[0] < i - 250:
                hq.popleft()
            hist_high = rows[hq[0]][2] if hq else None
            if hist_high and rows[i][4] > hist_high and rows[i - 1][4] <= hist_high and liq in ("L2 10-100万", "L3 >100万"):
                record("S5 突破 52 周新高", "首次收盘突破", rows, i, liq)
        if i + 5 < n and date.fromisoformat(d).weekday() == 4 and liq in ("L2 10-100万", "L3 >100万"):
            p5 = rows[i][4] / rows[i - 5][4] - 1
            if rows[i + 1][5] > 0 and abs(p5) < 3:
                nx = rows[i + 5][4] / rows[i + 1][1] - 1
                if abs(nx) < 3:
                    WEEKLY[d].append((p5, nx, liq, rows[i][6]))
    return events


def weekly_study():
    for d, items in WEEKLY.items():
        if len(items) < 50:
            continue
        items.sort(key=lambda t: t[0])
        k = len(items) // 10
        for name, grp in (("过去一周跌最多 10%", items[:k]), ("过去一周涨最多 10%", items[-k:])):
            for p5, nx, liq, px in grp:
                STUDIES[("S2 一周涨跌排名(周五收盘排名, 下周一开盘买, 持有一周)", name, "全部", 5)].add(d, nx, nx - cost(px, liq))


def index_studies(xjo, spx):
    rows = xjo
    n = len(rows)
    for i in range(1, n - 5):
        d = rows[i][0]
        if d < "2015-01-01":
            continue
        r = rows[i][4] / rows[i - 1][4] - 1
        wd = ["周一", "周二", "周三", "周四", "周五"][date.fromisoformat(d).weekday()] if date.fromisoformat(d).weekday() < 5 else None
        if wd:
            STUDIES[("M1 大盘星期几", wd, "XJO", 0)].add(d, r, r)
        nxt = rows[i + 1][0]
        if nxt[:7] != d[:7]:
            tag = "月末"
        elif d[:7] != rows[i - 1][0][:7] or (i >= 2 and d[:7] != rows[i - 2][0][:7]) or (i >= 3 and d[:7] != rows[i - 3][0][:7]):
            tag = "月初前3天"
        else:
            tag = "其他日子"
        STUDIES[("M2 月末月初效应", tag, "XJO", 0)].add(d, r, r)
        for th, tag in ((-.03, "大盘单日跌≥3%"), (-.015, "大盘单日跌≥1.5%"), (.015, "大盘单日涨≥1.5%")):
            hit = r <= th if th < 0 else r >= th
            if hit:
                for h in HORIZONS:
                    g = rows[i + h][4] / rows[i][4] - 1
                    STUDIES[("M3 大盘大波动之后(收盘买)", tag, "XJO", h)].add(d, g, g)
        if i >= 3 and all(rows[k][4] < rows[k - 1][4] for k in (i, i - 1, i - 2)):
            for h in HORIZONS:
                g = rows[i + h][4] / rows[i][4] - 1
                STUDIES[("M3 大盘大波动之后(收盘买)", "连跌 3 天", "XJO", h)].add(d, g, g)
    # US overnight -> ASX
    us = [(r[0], r[4]) for r in spx]
    us_ret = {us[k][0]: us[k][1] / us[k - 1][1] - 1 for k in range(1, len(us))}
    us_dates = sorted(us_ret)
    j, pairs = 0, []
    for i in range(1, n):
        d = rows[i][0]
        while j + 1 < len(us_dates) and us_dates[j + 1] < d:
            j += 1
        if not us_dates or us_dates[j] >= d or d < "2015-01-01":
            continue
        u = us_ret[us_dates[j]]
        gap = rows[i][1] / rows[i - 1][4] - 1
        intraday = rows[i][4] / rows[i][1] - 1
        cc = rows[i][4] / rows[i - 1][4] - 1
        pairs.append((d, u, gap, intraday, cc))
        for lo, hi, tag in ((-9, -.02, "美股跌≥2%"), (-.02, -.01, "美股跌1-2%"), (-.01, .01, "美股±1%内"), (.01, .02, "美股涨1-2%"), (.02, 9, "美股涨≥2%")):
            if lo <= u < hi:
                STUDIES[("M4 美股隔夜→澳股次日", tag + " · 澳股开盘跳空", "XJO", 0)].add(d, gap, gap)
                STUDIES[("M4 美股隔夜→澳股次日", tag + " · 澳股开盘到收盘", "XJO", 0)].add(d, intraday, intraday)
    corr = {}
    if len(pairs) > 30:
        def cr(a, b):
            ma, mb = sum(a) / len(a), sum(b) / len(b)
            num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
            return num / den if den else 0
        u = [p[1] for p in pairs]
        corr = {"美股→澳股开盘跳空": cr(u, [p[2] for p in pairs]),
                "美股→澳股开盘后到收盘": cr(u, [p[3] for p in pairs]),
                "美股→澳股全天": cr(u, [p[4] for p in pairs]), "样本天数": len(pairs)}
    return corr


# ---------------------------------------------------------------- report
def build_results(meta, corr, vol):
    table = []
    for (study, bucket, group, h), agg in STUDIES.items():
        full = agg.summary()
        if not full or full["n"] < 30:
            continue
        a, b = agg.summary(hi=SPLIT), agg.summary(lo=SPLIT)
        robust = bool(a and b and a["n"] >= 30 and b["n"] >= 30 and a["net"] * b["net"] > 0
                      and abs(a["t"]) >= 2 and abs(b["t"]) >= 2)
        table.append({"study": study, "bucket": bucket, "group": group, "h": h,
                      "all": full, "in": a, "out": b, "robust": robust})
    table.sort(key=lambda r: (r["study"], r["bucket"], r["group"], r["h"]))
    return {"meta": meta, "rows": table, "us_corr": corr, "vol": vol,
            "findings": findings(table, corr)}


def findings(table, corr):
    out = []
    seen = set()
    uniq = []
    for r in sorted(table, key=lambda r: r["group"] != "全部"):
        k = (r["study"], r["bucket"], r["h"]) if r["group"] == "全部" or (r["study"], r["bucket"], r["h"]) not in seen else None
        if k is None:
            continue
        if r["group"] != "全部":
            k = (r["study"], r["bucket"], r["h"], r["group"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    table_u = uniq
    tradable = [r for r in table_u if r["robust"] and r["group"] in ("L2 10-100万", "L3 >100万", "全部", "XJO")
                and r["h"] in (1, 2, 5) and not r["study"].startswith("M")]
    pos = sorted([r for r in tradable if r["all"]["net"] > 0], key=lambda r: -r["all"]["t"])[:6]
    for r in pos:
        out.append(("✅ 扣成本后仍有正收益，且两个时期都成立",
                    f"{r['study']} · {r['bucket']} · {r['group']} · 持有 {r['h']} 天：平均净收益 {r['all']['net']:+.2f}%，胜率 {r['all']['win']:.0f}%，"
                    f"样本 {r['all']['n']} 次（2015-21 年 {r['in']['net']:+.2f}%，2022 年后 {r['out']['net']:+.2f}%）。"))
    neg = sorted([r for r in tradable if r["all"]["net"] < 0], key=lambda r: r["all"]["t"])[:6]
    for r in neg:
        out.append(("⛔ 稳定亏钱：应该避开，或者反着看",
                    f"{r['study']} · {r['bucket']} · {r['group']} · 持有 {r['h']} 天：平均净收益 {r['all']['net']:+.2f}%，胜率 {r['all']['win']:.0f}%，"
                    f"样本 {r['all']['n']} 次。"))
    cheap = [r for r in table if r["group"] in ("L0 <1万", "L1 1-10万") and r["h"] == 2 and r["all"]["gross"] > 0 and r["all"]["net"] < 0]
    if cheap:
        r = max(cheap, key=lambda r: r["all"]["gross"])
        out.append(("⚠️ 账面赚钱，扣成本就亏",
                    f"{r['study']} · {r['bucket']} · {r['group']}：毛收益 {r['all']['gross']:+.2f}%，扣买卖价差和手续费后 {r['all']['net']:+.2f}%。冷门小票的规律大多吃不到。"))
    if corr:
        a, b = corr["美股→澳股开盘跳空"], corr["美股→澳股开盘后到收盘"]
        tail = ("美股的影响基本在开盘那一下就体现完了，开盘后再追意义不大。" if abs(a) > .3 and abs(b) < .1
                else "开盘后还会继续沿美股方向走。" if b > .1 else "开盘后往往会回吐一部分。" if b < -.1 else "两者关系不明显。")
        out.append(("🌏 美股隔夜对澳股",
                    f"标普 500 前一晚的涨跌，和澳股第二天开盘跳空的相关系数是 {a:.2f}，和开盘后到收盘的走势是 {b:+.2f}。{tail}"))
    m = {r["bucket"]: r for r in table if r["study"].startswith("M3") and r["h"] == 5}
    if "大盘单日跌≥3%" in m:
        r = m["大盘单日跌≥3%"]
        out.append(("📉 大盘大跌之后",
                    f"ASX 200 单日跌 3% 以上，之后 5 天平均 {r['all']['gross']:+.2f}%，上涨概率 {r['all']['win']:.0f}%，共 {r['all']['n']} 次。样本少，只能当参考。"))
    if not pos:
        out.append(("ℹ️ 结论", "扣成本后，没有哪个简单的短线规律在两个时期都稳定赚钱。这本身是有价值的结论：短线大多数“规律”是噪音或被成本吃掉。"))
    return [{"tag": t, "text": x} for t, x in out]


def main():
    if not KEY:
        sys.exit("没有找到 EODHD_API_KEY，请先在 .env 里填好。")
    t0 = time.time()
    print("第 1 步：获取股票名单（含已退市）…")
    uni = universe()
    print(f"  共 {len(uni)} 只普通股，其中已退市 {sum(1 for _, d in uni if d)} 只")
    syms = [f"{c}.AU" for c, _ in uni] + ["AXJO.INDX", "GSPC.INDX"]
    print("第 2 步：下载 2014 年至今的日线（可中断，下次会接着下）…")
    download(syms)
    print("第 3 步：逐只股票分析…")
    stocks, events = 0, 0
    for k, (code, _) in enumerate(uni):
        rows = load(f"{code}.AU")
        if rows:
            stocks += 1
            events += scan_stock(rows)
        if (k + 1) % 200 == 0:
            print(f"\r  已分析 {k + 1}/{len(uni)}", end="", flush=True)
    print()
    weekly_study()
    print("第 4 步：大盘和美股…")
    xjo, spx = load("AXJO.INDX"), load("GSPC.INDX")
    corr = index_studies(xjo, spx) if xjo and spx else {}
    vol = None
    if xjo:
        from research import analyse
        vol = analyse("XJO", [{"date": r[0], "close": r[4]} for r in xjo])
    meta = {"generated": time.strftime("%Y-%m-%d %H:%M"), "stocks": stocks,
            "delisted": sum(1 for _, d in uni if d), "events": events,
            "start": "2015-01-01", "split": SPLIT, "commission": COMMISSION,
            "spread": SPREAD, "minutes": round((time.time() - t0) / 60, 1)}
    res = build_results(meta, corr, vol)
    out = Path(os.environ.get("STUDY_OUT", DATA / "study_results.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"\n完成，用时 {meta['minutes']} 分钟。结果: {out}")
    print("打开 http://127.0.0.1:8765/study.html 查看报告（需要 start 在运行）。")


if __name__ == "__main__":
    main()
