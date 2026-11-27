"""Independent re-computation of 3 cells of the round-104 liquidation table (acceptance check, Frank 2026-11-27).

Written separately from crypto_risk.py on purpose: plain Python loops, one trade at a time, its own file reading and
its own funding alignment, no numpy. Prints both numbers side by side; the round passes only if they agree.
"""
import calendar
import json
import time
from pathlib import Path

MM = {"BTCUSDT": 0.0125, "ETHUSDT": 0.02}
COST = 0.0007
CELLS = [("BTCUSDT", "long", 5, 0, 7), ("ETHUSDT", "short", 3, 5, 3), ("BTCUSDT", "long", 2, 2, 30)]


def one(sym, side, L, stop_pct, days):
    K = json.loads(Path(f"data/crypto/{sym}_1h.json").read_text())
    F = json.loads(Path(f"data/crypto/{sym}_funding.json").read_text())
    start = calendar.timegm(time.strptime("2019-09-01", "%Y-%m-%d"))
    K = [r for r in K if r[0] >= start]
    hour_of = {r[0]: i for i, r in enumerate(K)}
    fund = [0.0] * len(K)
    for ft, rate in F:
        x = ft - 1800                      # first hour bar starting at or after half an hour before the print
        hr = x if x % 3600 == 0 else (x // 3600 + 1) * 3600
        if hr in hour_of:
            fund[hour_of[hr]] += rate
    H, hmax = days * 24, 720
    sgn = 1 if side == "long" else -1
    liq_d = 1 / L - MM[sym]
    s = stop_pct / 100 if stop_pct else None
    n = len(K) - hmax
    n_stop = n_liq = 0
    total = 0.0
    for i in range(n):
        e = K[i][4]
        res, paid = None, 0.0
        for k in range(1, H + 1):
            o, hi, lo = K[i + k][1], K[i + k][2], K[i + k][3]
            paid += fund[i + k]
            worst = lo / e - 1 if side == "long" else 1 - hi / e      # most adverse move this hour (negative)
            hit_liq = worst <= -liq_d
            hit_stop = s is not None and worst <= -s
            if hit_stop and (not hit_liq or s < liq_d):
                fillp = min(e * (1 - s), o) if side == "long" else max(e * (1 + s), o)
                res = ("stop", max(-1.0, L * (sgn * (fillp / e - 1) - sgn * paid) - L * 2 * COST))
                break
            if hit_liq:
                res = ("liq", -1.0)
                break
        if res is None:
            res = ("end", max(-1.0, L * (sgn * (K[i + H][4] / e - 1) - sgn * paid) - L * 2 * COST))
        n_stop += res[0] == "stop"
        n_liq += res[0] == "liq"
        total += res[1]
    return {"n": n, "stop": round(100 * n_stop / n, 2), "liq": round(100 * n_liq / n, 2), "mean": round(100 * total / n, 2)}


if __name__ == "__main__":
    import os
    tab = json.loads(Path("data/crypto_risk.json").read_text())["symbols"]
    out = []
    for sym, side, L, sp, d in CELLS:
        mine = one(sym, side, L, sp, d)
        theirs = tab[sym]["table"][f"{side}|{L}|{sp}|{d}"]
        same = all(abs(mine[k] - theirs[k]) <= 0.01 for k in ("stop", "liq", "mean")) and mine["n"] == theirs["n"]
        out.append({"cell": f"{sym} {side} {L}x stop {sp}% {d}d", "independent": mine,
                    "table": {k: theirs[k] for k in ("n", "stop", "liq", "mean")}, "match": same})
        print("checked", out[-1]["cell"], flush=True)
    if os.environ.get("CRYPTO_CHECK_OUT"):
        Path(os.environ["CRYPTO_CHECK_OUT"]).write_text(json.dumps(out, ensure_ascii=False))
