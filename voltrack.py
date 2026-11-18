"""波动率预测实盘记录 (live volatility-forecast track): every week keep the volatility model's forecasts, and once the
next 5 / 20 trading sessions have happened, compare them with what really happened -> weekly hit rate.

Like tracklog.py this cannot be redone later: it only exists for the weeks we recorded (started 2026-11-18).
File: out/vol_track.json = {"started", "weeks": {"<forecast date>": {"items": [...], "score": {...}}}, "summary": {...}}
- One snapshot per ISO week: a later run in the same week replaces the earlier one, so the week ends with
  the Friday close forecast ("下周波动率预测").
- Realised volatility = sqrt(sum of squared daily log returns of the next h traded sessions / h * 252), adjusted closes,
  zero-volume (halted) days skipped -- the same yardstick as volml's targets.
Hit definitions (fixed before seeing any result):
  命中 (hit)        forecast within +/-25% of realised (e.g. said 40%, actual 32%-53.3% counts)
  胜过简单法 (beat)  forecast closer than "past 20-day vol" in log terms
  方向对 (dir)       forecast said up/down versus past 20-day vol and realised went the same way
"""
import json
import math
from datetime import date
from pathlib import Path

import study

HIT_BAND = 0.25
HORIZONS = (5, 20)


def _path():
    return Path("out") / "vol_track.json"


def load():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except Exception:
        return {"started": date.today().isoformat(), "weeks": {}}


def save(T):
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")


def _week(d):
    y, w, _ = date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def record(T, latest):
    """latest: volml results 'latest' list. Keeps one snapshot per ISO week (the newest forecast date wins)."""
    items = [{k: x.get(k) for k in ("code", "sector", "vol5", "naive_vol5", "vol20", "naive_vol20", "pct20")}
             for x in latest if x.get("vol5") and x.get("date")]
    if not items:
        return None
    day = max(x["date"] for x in latest if x.get("date"))
    wk = _week(day)
    for d in [d for d in T["weeks"] if _week(d) == wk and d != day and not T["weeks"][d].get("score")]:
        del T["weeks"][d]
    if day not in T["weeks"] or not T["weeks"][day].get("score"):
        T["weeks"][day] = {"items": items}
    return day


def realised(code, day, h, cache=None):
    """Annualised % volatility over the h traded sessions after `day`, or None if they have not all happened yet."""
    rows = (cache or {}).get(code)
    if rows is None:
        f = study.CACHE / f"{code}.AU.json"
        if not f.exists():
            return None
        rows = [(r[0], r[5]) for r in json.loads(f.read_text(encoding="utf-8")) if r[5] and r[5] > 0 and r[6]]
        if cache is not None:
            cache[code] = rows
    base = None
    after = []
    for d, a in rows:
        if d <= day:
            base = a
        elif base:
            after.append(a)
            if len(after) == h:
                break
    if base is None or len(after) < h:
        return None
    px = [base] + after
    s = sum(math.log(px[i + 1] / px[i]) ** 2 for i in range(h))
    return math.sqrt(s / h * 252) * 100


def score_items(items, h, real):
    """real(code) -> realised vol or None. Returns the hit counts for horizon h."""
    n = hit = beat = dn = dok = 0
    for it in items:
        f, nv = it.get(f"vol{h}"), it.get(f"naive_vol{h}")
        y = real(it["code"])
        if not f or y is None or y <= 0:
            continue
        n += 1
        hit += abs(f / y - 1) <= HIT_BAND
        if nv:
            beat += abs(math.log(f / y)) < abs(math.log(nv / y))
            if f != nv and y != nv:
                dn += 1
                dok += (f > nv) == (y > nv)
    if not n:
        return None
    return {"n": n, "hit": round(100 * hit / n, 1), "beat_naive": round(100 * beat / n, 1),
            "direction": round(100 * dok / dn, 1) if dn else None}


def score(T, cache=None):
    cache = {} if cache is None else cache
    for day, W in T["weeks"].items():
        sc = W.setdefault("score", {})
        for h in HORIZONS:
            if str(h) in sc:
                continue
            r = score_items(W["items"], h, lambda c: realised(c, day, h, cache))
            if r:
                sc[str(h)] = r
    return T


def summarise(T):
    weeks = sorted(d for d, W in T["weeks"].items() if (W.get("score") or {}).get("5"))
    hist = [{"date": d, **T["weeks"][d]["score"]["5"]} for d in weeks]
    last4 = hist[-4:]
    # 降级规则: hit rate not better than 4 weeks earlier, four weeks in a row
    stalled = len(hist) >= 8 and all(hist[-i]["hit"] <= hist[-i - 4]["hit"] for i in range(1, 5))
    pending = sorted(d for d, W in T["weeks"].items() if not (W.get("score") or {}).get("5"))
    T["summary"] = {"asof": date.today().isoformat(), "band": f"±{int(HIT_BAND * 100)}%",
                    "weeks_scored": len(hist), "history": hist,
                    "last": hist[-1] if hist else None,
                    "avg4_hit": round(sum(x["hit"] for x in last4) / len(last4), 1) if last4 else None,
                    "pending": pending, "stalled_4_weeks": stalled}
    return T["summary"]


def run(latest):
    T = load()
    record(T, latest)
    score(T)
    s = summarise(T)
    save(T)
    return s
