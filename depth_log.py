"""盘口快照 + 12 月 19 日小检验（Frank 2026-11-24 下单；盈透 ASX Total 只试用到 2026-12-20）。

规则先写死在这里，之后不看结果改参数。

一、拍什么
- 时间：每个 ASX 交易日 10:30、13:00、15:55（墨尔本时间）。到点后 20 分钟内拍完；过了 20 分钟还没拍成，记"缺"和原因。
- 名单（按这个顺序取，去重，最多 60 只）：
  1. Frank 设了到价提醒的股票；
  2. 并购套利看板（第 83 轮，merger_board.json）里的目标公司；
  3. 🚀 底部放量突破（第 98 轮 R4，ladder_lists.json 的 r4）；
  4. 🌱 底部弹簧（R1）；
  5. 爆拉候选（每日扫描 study22_results.json 的 rockets）。
  名单在每次拍照前现取（10 分钟缓存），取不到的那一类记进快照的 list_errors。
- 每只记：买卖各 5 档的价格和数量；买一卖一价差（绝对值和占中间价的百分比）；买 5 档总量、卖 5 档总量、
  买卖盘总量比 = 买总量 / 卖总量、失衡度 I = (买总量 − 卖总量) / (买总量 + 卖总量)。
- 盈透同时能开的盘口订阅有限（非专业账户通常 3 个）。第一次拍照时实测：最多同时开 10 个，收到 309 错误
  （"Max number of market depth requests"）的不算，数出真正的上限，记进 depth_slots；之后按这个数分批轮流拍，
  每批等盘口填满（最多 4 秒），记下每批实际用时。

二、存哪儿
- Frank 电脑的程序文件夹 out/depth/YYYY-MM-DD.json.enc，用 ASX_DATA_KEY 加密（securedata.py）；只增不删：
  同一天的第二、三次快照追加进同一个文件，旧的不改。没有密钥就不存（不留明文），记一条错误。
- 缺拍记进 out/depth/missing.json（日期、时间点、原因），只增不删。电脑或终端没开的日子没有任何记录，
  检验时按"无记录 = 电脑 / 终端没开"补上。

三、12 月 19 日小检验（结果只能标"初步"，不准据此交易）
- 只用 15:55 那一张。样本 = 当天有 15:55 快照、买卖两边都至少有 1 档、价差有效（卖一 > 买一）的股票。
- 收益（EODHD 复权日线）：
  收盘到收盘 = 第二个交易日收盘 / 当天收盘 − 1；
  开盘到收盘 = 第二个交易日收盘 / 第二个交易日开盘 − 1。
- 成本：一次来回扣一个完整的买一卖一价差（15:55 那一刻的价差 / 中间价）。
- 主检验：每天算失衡度 I 和两种收益的秩相关（Spearman），按天取平均，t = 平均 / 标准差 × √天数（相当于按日期聚类）。
- 副检验：每天按 I 分三组，看最高一组减最低一组的平均收益；最高一组扣成本后的平均收益。
- 判定：主检验 t ≥ 2 且副检验同方向、最高一组扣成本后 > 0，才写"初步有信号"；否则写"没看到信号"。
  样本少于 10 个交易日或少于 300 个股票日，只写"样本不够"。
"""
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

SNAP_TIMES = ("10:30", "13:00", "15:55")
WINDOW_MIN = 20          # minutes after the slot in which the snapshot may still be taken
MAX_CODES = 60
LEVELS = 5


def melb_now():
    """Melbourne local time. Windows Python often lacks the tz database (no tzdata), so fall back to the PC clock,
    which is set to Melbourne on Frank's computer."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Australia/Melbourne")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def is_weekday(dt):
    return dt.weekday() < 5


def due_slot(now, done):
    """The snapshot slot that should be taken now ('10:30' ...), or None. done: set of slots already taken (or given up)
    today."""
    if not is_weekday(now):
        return None
    for s in SNAP_TIMES:
        h, m = map(int, s.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if s not in done and start <= now < start + timedelta(minutes=WINDOW_MIN):
            return s
    return None


def missed_slots(now, done):
    """Slots of today whose window has passed without a snapshot."""
    if not is_weekday(now):
        return []
    out = []
    for s in SNAP_TIMES:
        h, m = map(int, s.split(":"))
        if s not in done and now >= now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(minutes=WINDOW_MIN):
            out.append(s)
    return out


def pick_codes(sources, cap=MAX_CODES):
    """sources: list of (tag, [codes]) in priority order -> ordered [(code, [tags])], at most cap codes."""
    seen = {}
    for tag, codes in sources:
        for c in codes or []:
            c = str(c).upper().strip()
            if not c or not c.isalnum() or len(c) > 6:
                continue
            if c in seen:
                if tag not in seen[c]:
                    seen[c].append(tag)
            elif len(seen) < cap:
                seen[c] = [tag]
    return list(seen.items())


def summarise_book(bids, asks):
    """bids / asks: [(price, size), ...] best first. Returns the stored record for one stock."""
    bids = [(float(p), float(s)) for p, s in bids[:LEVELS] if p and p > 0 and s and s > 0]
    asks = [(float(p), float(s)) for p, s in asks[:LEVELS] if p and p > 0 and s and s > 0]
    bt, at = sum(s for _, s in bids), sum(s for _, s in asks)
    rec = {"bids": [[p, s] for p, s in bids], "asks": [[p, s] for p, s in asks], "bid_total": bt, "ask_total": at,
           "ratio": round(bt / at, 4) if at else None,
           "imb": round((bt - at) / (bt + at), 4) if bt + at else None}
    if bids and asks and asks[0][0] > bids[0][0]:
        mid = (asks[0][0] + bids[0][0]) / 2
        rec["spread"] = round(asks[0][0] - bids[0][0], 6)
        rec["spread_pct"] = round(100 * rec["spread"] / mid, 4)
        rec["mid"] = mid
    return rec


# ---- storage (encrypted, append-only) -------------------------------------------------------------------------

def day_file(folder, day):
    return Path(folder) / f"{day}.json.enc"


def load_day(folder, day, key=None):
    import securedata
    f = day_file(folder, day)
    if not f.exists():
        return {"day": day, "snapshots": []}
    return json.loads(securedata.decrypt(f.read_bytes(), key))


def append_snapshot(folder, day, snap, key=None):
    """Add one snapshot to the day's encrypted file. Earlier snapshots are kept as they are (append-only).
    Raises if there is no key: never store the book in plain text."""
    import securedata
    if securedata._keys(key) is None:
        raise RuntimeError("没有 ASX_DATA_KEY，盘口快照不存（不留明文）")
    Path(folder).mkdir(parents=True, exist_ok=True)
    data = load_day(folder, day, key)
    data["snapshots"].append(snap)
    tmp = day_file(folder, day).with_suffix(".tmp")
    tmp.write_bytes(securedata.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"), key))
    tmp.replace(day_file(folder, day))
    return len(data["snapshots"])


def log_missing(folder, day, slot, reason):
    f = Path(folder) / "missing.json"
    Path(folder).mkdir(parents=True, exist_ok=True)
    rows = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    if any(r["day"] == day and r["slot"] == slot for r in rows):
        return
    rows.append({"day": day, "slot": slot, "reason": reason, "logged": time.strftime("%Y-%m-%d %H:%M")})
    f.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


# ---- 12 月 19 日小检验 ------------------------------------------------------------------------------------------

def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 5:
        return None
    ra, rb = _rank(a), _rank(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else None


def _t(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 3:
        return None, len(xs)
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return (m / sd * math.sqrt(len(xs)) if sd else None), len(xs)


def analyse(days, bars):
    """days: {day: day_data as stored}; bars: {code: [(date, open, close_adj), ...] sorted}. Pre-registered test above."""
    res = {"label": "初步", "rule": "15:55 失衡度 I 对第二天收益；成本 = 一个完整价差；t 按天聚类", "per_day": []}
    ic = {"cc": [], "oc": []}
    spread3 = {"cc": [], "oc": []}
    top_net = {"cc": [], "oc": []}
    n_obs = 0
    for day in sorted(days):
        snap = next((s for s in days[day].get("snapshots", []) if s.get("slot") == "15:55"), None)
        if not snap:
            continue
        rows = []
        for code, rec in (snap.get("books") or {}).items():
            if rec.get("imb") is None or rec.get("spread_pct") is None:
                continue
            b = bars.get(code) or []
            idx = next((i for i, x in enumerate(b) if x[0] == day), None)
            if idx is None or idx + 1 >= len(b):
                continue
            _, _, c0 = b[idx]
            _, o1, c1 = b[idx + 1]
            if not (c0 and o1 and c1):
                continue
            rows.append((rec["imb"], c1 / c0 - 1, c1 / o1 - 1, rec["spread_pct"] / 100))
        if len(rows) < 6:
            continue
        n_obs += len(rows)
        imb = [r[0] for r in rows]
        d = {"day": day, "n": len(rows)}
        for k, col in (("cc", 1), ("oc", 2)):
            ret = [r[col] for r in rows]
            s = spearman(imb, ret)
            ic[k].append(s)
            srt = sorted(rows, key=lambda r: r[0])
            third = len(srt) // 3
            lo, hi = srt[:third], srt[-third:]
            hm = sum(r[col] for r in hi) / len(hi)
            lm = sum(r[col] for r in lo) / len(lo)
            spread3[k].append(hm - lm)
            top_net[k].append(sum(r[col] - r[3] for r in hi) / len(hi))
            d[k] = {"ic": round(s, 3) if s is not None else None, "top_minus_bottom_pct": round(100 * (hm - lm), 2)}
        res["per_day"].append(d)
    res["days"] = len(res["per_day"])
    res["stock_days"] = n_obs
    for k, name in (("cc", "收盘到收盘"), ("oc", "开盘到收盘")):
        t, n = _t(ic[k])
        mean_ic = sum(x for x in ic[k] if x is not None) / max(1, len([x for x in ic[k] if x is not None]))
        m3 = sum(spread3[k]) / len(spread3[k]) if spread3[k] else None
        tn = sum(top_net[k]) / len(top_net[k]) if top_net[k] else None
        res[k] = {"name": name, "mean_ic": round(mean_ic, 4), "t": round(t, 2) if t is not None else None,
                  "top_minus_bottom_pct": round(100 * m3, 3) if m3 is not None else None,
                  "top_net_of_spread_pct": round(100 * tn, 3) if tn is not None else None}
        if res["days"] < 10 or n_obs < 300:
            verdict = "样本不够"
        elif t is not None and t >= 2 and m3 and m3 > 0 and tn and tn > 0:
            verdict = "初步有信号"
        else:
            verdict = "没看到信号"
        res[k]["verdict"] = verdict
    return res
