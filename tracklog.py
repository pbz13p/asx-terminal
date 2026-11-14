"""Live track record ("实盘记录"): every signal the daily jobs publish is written down with the date and price,
then followed forward. Unlike a backtest this cannot be redone later -- it only exists for the days we recorded.

Each market keeps out/track_<market>.json: {"entries": [...], "summary": {...}}.
An entry: {"d": signal date, "list": list id, "code", "px": price at the signal (close), "bench": index level}.
A code is not re-entered on the same list while an earlier entry is younger than 60 days.
"""
import json
from datetime import date
from pathlib import Path

HOLD_DAYS = (20, 60, 120)  # trading days for the summary checkpoints


def _path(market):
    return Path("out") / f"track_{market}.json"


def load(market):
    p = _path(market)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"started": date.today().isoformat(), "entries": []}


def adj_return(rows, day):
    """rows: [(date, adjusted close), ...] ascending. Return from the close on/just before `day` to the last close."""
    base = None
    for d, c in rows:
        if d <= day:
            base = c
        else:
            break
    return (rows[-1][1] / base - 1) if base and rows else None


def record(market, list_id, day, items, bench):
    """items: [{"code", "px"}]; bench: index level on `day`."""
    T = load(market)
    recent = {}
    for e in T["entries"]:
        if e["list"] == list_id:
            recent[e["code"]] = max(recent.get(e["code"], ""), e["d"])
    added = 0
    for it in items:
        last = recent.get(it["code"])
        if last and (date.fromisoformat(day) - date.fromisoformat(last)).days < 60:
            continue
        if not it.get("px"):
            continue
        T["entries"].append({"d": day, "list": list_id, "code": it["code"], "px": float(it["px"]), "bench": bench})
        added += 1
    T.setdefault("started", day)
    _path(market).parent.mkdir(parents=True, exist_ok=True)
    _path(market).write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    return added


def summarise(market, ret_since, bench_now, labels):
    """ret_since(code, day) -> return from that day's close to the latest close (split/dividend adjusted) or None;
    bench_now -> index level today. Writes the summary into the file."""
    T = load(market)
    out = {}
    for list_id, label in labels.items():
        E = [e for e in T["entries"] if e["list"] == list_id]
        rets, ex = [], []
        for e in E:
            r = ret_since(e["code"], e["d"])
            if r is None:
                continue
            rets.append(r)
            if e.get("bench") and bench_now:
                ex.append(r - (bench_now / e["bench"] - 1))
        n = len(rets)
        out[list_id] = {"label": label, "signals": len(E), "priced": n,
                        "first": min((e["d"] for e in E), default=None),
                        "avg_ret": round(100 * sum(rets) / n, 2) if n else None,
                        "win_rate": round(100 * sum(1 for r in rets if r > 0) / n) if n else None,
                        "avg_vs_index": round(100 * sum(ex) / len(ex), 2) if ex else None,
                        "best": round(100 * max(rets), 1) if n else None, "worst": round(100 * min(rets), 1) if n else None}
    T["summary"] = {"asof": date.today().isoformat(), "lists": out}
    _path(market).write_text(json.dumps(T, ensure_ascii=False), encoding="utf-8")
    return T["summary"]
