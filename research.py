"""Volatility research on daily closes. Pure Python, no extra packages."""
import math
from datetime import date

ANN = math.sqrt(252)
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五"]


def _mean(a):
    return sum(a) / len(a) if a else 0.0


def _std(a):
    if len(a) < 2:
        return 0.0
    m = _mean(a)
    return math.sqrt(sum((x - m) ** 2 for x in a) / (len(a) - 1))


def _pct_rank(a, v):
    return 100.0 * sum(1 for x in a if x <= v) / len(a) if a else 0.0


def _quantile(a, q):
    b = sorted(a)
    if not b:
        return 0.0
    i = (len(b) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return b[lo] + (b[hi] - b[lo]) * (i - lo)


def _rolling_vol(r, n):
    out, s, s2 = [None] * len(r), 0.0, 0.0
    for i, x in enumerate(r):
        s += x
        s2 += x * x
        if i >= n:
            s -= r[i - n]
            s2 -= r[i - n] ** 2
        if i >= n - 1:
            var = max(0.0, (s2 - s * s / n) / (n - 1))
            out[i] = math.sqrt(var) * ANN
    return out


def _autocorr(a, lag):
    n = len(a) - lag
    if n < 10:
        return 0.0
    x, y = a[:n], a[lag:]
    mx, my = _mean(x), _mean(y)
    num = sum((p - mx) * (q - my) for p, q in zip(x, y))
    den = math.sqrt(sum((p - mx) ** 2 for p in x) * sum((q - my) ** 2 for q in y))
    return num / den if den else 0.0


def analyse(code, rows):
    dates = [r["date"] for r in rows]
    px = [float(r["close"]) for r in rows]
    r = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    rd = dates[1:]
    sd = _std(r)
    m = _mean(r)

    rv20, rv60 = _rolling_vol(r, 20), _rolling_vol(r, 60)
    rv20_hist = [v for v in rv20 if v is not None]
    cur20, cur60 = rv20_hist[-1], [v for v in rv60 if v is not None][-1]
    pct20 = _pct_rank(rv20_hist, cur20)

    step = max(1, len(r) // 900)
    series = [{"d": rd[i], "v": round(rv20[i] * 100, 2)} for i in range(0, len(r), step) if rv20[i] is not None]
    if series[-1]["d"] != rd[-1]:
        series.append({"d": rd[-1], "v": round(cur20 * 100, 2)})

    cone = []
    for n in (10, 20, 60, 120, 250):
        h = [v for v in _rolling_vol(r, n) if v is not None]
        cone.append({"w": n, "min": _quantile(h, 0) * 100, "p25": _quantile(h, .25) * 100,
                     "med": _quantile(h, .5) * 100, "p75": _quantile(h, .75) * 100,
                     "max": _quantile(h, 1) * 100, "now": h[-1] * 100})

    z = [(x - m) / sd for x in r] if sd else [0] * len(r)
    bins = [0] * 41
    for v in z:
        k = int(round(max(-5, min(5, v)) * 4)) + 20
        bins[k] += 1
    skew = _mean([v ** 3 for v in z])
    kurt = _mean([v ** 4 for v in z]) - 3
    tail_n = sum(1 for v in z if abs(v) > 3)
    tail_exp = len(z) * 0.0027

    absr = [abs(x) for x in r]
    ac_abs = [round(_autocorr(absr, k), 3) for k in range(1, 21)]
    ac_ret = [round(_autocorr(r, k), 3) for k in range(1, 21)]

    wd = [[] for _ in range(5)]
    mo = [[] for _ in range(12)]
    for d, x in zip(rd, r):
        y, mm, dd = map(int, d.split("-"))
        w = date(y, mm, dd).weekday()
        if w < 5:
            wd[w].append(abs(x))
        mo[mm - 1].append(abs(x))
    weekday = [round(_mean(a) * 100, 3) for a in wd]
    month = [round(_mean(a) * 100, 3) for a in mo]

    peak, dd_series, max_dd, max_dd_date = px[0], [], 0.0, dates[0]
    for i, p in enumerate(px):
        peak = max(peak, p)
        d = p / peak - 1
        if d < max_dd:
            max_dd, max_dd_date = d, dates[i]
        if i % step == 0 or i == len(px) - 1:
            dd_series.append({"d": dates[i], "v": round(d * 100, 2)})

    pairs = []
    for i in range(len(r) - 20):
        if rv20[i] is not None:
            fwd_ret = px[i + 21] / px[i + 1] - 1
            fwd_vol = _std(r[i + 1:i + 21]) * ANN
            pairs.append((rv20[i], fwd_ret, fwd_vol))
    pairs.sort(key=lambda t: t[0])
    quint = []
    for q in range(5):
        chunk = pairs[len(pairs) * q // 5: len(pairs) * (q + 1) // 5]
        if chunk:
            quint.append({"lo": chunk[0][0] * 100, "hi": chunk[-1][0] * 100,
                          "ret": _mean([c[1] for c in chunk]) * 100,
                          "win": 100.0 * sum(1 for c in chunk if c[1] > 0) / len(chunk),
                          "vol": _mean([c[2] for c in chunk]) * 100})

    order = sorted(range(len(r)), key=lambda i: r[i])
    worst = [{"d": rd[i], "v": round((math.exp(r[i]) - 1) * 100, 2)} for i in order[:8]]
    best = [{"d": rd[i], "v": round((math.exp(r[i]) - 1) * 100, 2)} for i in order[::-1][:8]]

    level = "偏高" if pct20 >= 80 else "偏低" if pct20 <= 20 else "正常"
    findings = [
        f"现在 20 日年化波动 {cur20 * 100:.1f}%，比过去 {len(r) // 252} 年里 {pct20:.0f}% 的时候都高，属于{level}水平。",
        f"大跌大涨比正态分布常见：超过 3 倍标准差的日子有 {tail_n} 天，正态分布只会有约 {tail_exp:.0f} 天，是 {tail_n / max(tail_exp, 1e-9):.1f} 倍。",
        f"波动会扎堆：今天波动大，明天大概率也大（相关系数 {ac_abs[0]:.2f}），20 天后还剩 {ac_abs[19]:.2f}。涨跌方向本身几乎不能预测（相关系数 {ac_ret[0]:+.2f}）。",
        f"历史最大回撤 {max_dd * 100:.1f}%（{max_dd_date}），现在离最高点 {dd_series[-1]['v']:.1f}%。",
    ]
    if len(quint) == 5:
        findings.append(f"按波动高低分五组：最高波动组之后 20 天平均涨跌 {quint[4]['ret']:+.2f}%（上涨概率 {quint[4]['win']:.0f}%），最低组 {quint[0]['ret']:+.2f}%（{quint[0]['win']:.0f}%）。高波动之后的平均波动是 {quint[4]['vol']:.1f}%，低波动之后是 {quint[0]['vol']:.1f}%。")
    findings.append(f"一周里{WEEKDAYS[weekday.index(max(weekday))]}波动最大，{WEEKDAYS[weekday.index(min(weekday))]}最小；一年里 {month.index(max(month)) + 1} 月最大，{month.index(min(month)) + 1} 月最小。差别通常不大，别当成交易信号。")

    return {
        "code": code, "start": dates[0], "end": dates[-1], "days": len(r),
        "now": {"rv20": cur20 * 100, "rv60": cur60 * 100, "pct20": pct20,
                "dd": dd_series[-1]["v"], "maxdd": max_dd * 100, "maxdd_date": max_dd_date},
        "series": series, "cone": cone,
        "dist": {"bins": bins, "skew": skew, "kurt": kurt, "tail": tail_n, "tail_exp": tail_exp},
        "ac_abs": ac_abs, "ac_ret": ac_ret, "weekday": weekday, "month": month,
        "drawdown": dd_series, "quintiles": quint, "best": best, "worst": worst,
        "findings": findings,
    }
