"""python tests/test_voltrack.py -- weekly volatility-forecast track: record, realised vol, hit rates, weekly replace."""
import json
import math
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import study  # noqa: E402
import voltrack  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    os.chdir(tmp)
    study.CACHE = tmp / "eod"
    study.CACHE.mkdir()
    # AAA: daily log moves alternate +/-1% -> realised vol = 1% * sqrt(252) = 15.87%
    d0 = date(2026, 11, 2)
    days = [d0 + timedelta(days=i) for i in range(60) if (d0 + timedelta(days=i)).weekday() < 5]
    px, rows = 1.0, []
    for i, d in enumerate(days):
        px *= math.exp(0.01 if i % 2 else -0.01)
        rows.append([d.isoformat(), px, px, px, px, px, 1000])
    (study.CACHE / "AAA.AU.json").write_text(json.dumps(rows))
    assert abs(voltrack.realised("AAA", "2026-11-13", 5) - 15.87) < 0.05
    assert voltrack.realised("AAA", days[-2].isoformat(), 5) is None  # future not happened yet

    T = {"started": "2026-11-13", "weeks": {}}
    # Wednesday run then Friday run of the same week: only Friday kept
    voltrack.record(T, [{"code": "AAA", "date": "2026-11-11", "vol5": 99, "naive_vol5": 20, "vol20": 99, "naive_vol20": 20}])
    voltrack.record(T, [{"code": "AAA", "date": "2026-11-13", "vol5": 17, "naive_vol5": 30, "vol20": 25, "naive_vol20": 10},
                        {"code": "ZZZ", "date": "2026-11-13", "vol5": 50, "naive_vol5": 40}])
    assert list(T["weeks"]) == ["2026-11-13"], T["weeks"].keys()
    voltrack.score(T)
    s5 = T["weeks"]["2026-11-13"]["score"]["5"]
    # AAA: 17 vs 15.87 -> hit, beats naive 30, direction right (said down from 30, went down). ZZZ has no prices.
    assert s5 == {"n": 1, "hit": 100.0, "beat_naive": 100.0, "direction": 100.0}, s5
    s20 = T["weeks"]["2026-11-13"]["score"]["20"]
    # 25 vs 15.87 -> miss (+58%); naive 10 is closer in log terms? |ln(25/15.87)|=0.45 vs |ln(10/15.87)|=0.46 -> beat
    assert s20["hit"] == 0.0 and s20["beat_naive"] == 100.0 and s20["direction"] == 100.0, s20
    s = voltrack.summarise(T)
    assert s["weeks_scored"] == 1 and s["last"]["hit"] == 100.0 and not s["stalled_4_weeks"]
    assert s["history"][0]["h20"]["hit"] == 0.0 and s["change"] is None
    # scored weeks are frozen: a re-record does not overwrite them
    voltrack.record(T, [{"code": "AAA", "date": "2026-11-13", "vol5": 99, "naive_vol5": 30}])
    assert T["weeks"]["2026-11-13"]["items"][0]["vol5"] == 17
    # stall rule: 8 weeks, last 4 each no better than 4 weeks earlier
    T2 = {"weeks": {f"2026-0{1 + i // 4}-{10 + i % 4:02d}": {"score": {"5": {"n": 10, "hit": h}}}
                    for i, h in enumerate([50, 55, 60, 52, 50, 55, 60, 52])}}
    s2 = voltrack.summarise(T2)
    assert s2["stalled_4_weeks"] is True and s2["change"] == -8 and s2["history"][0]["h20"] is None
    voltrack.save(T)
    assert voltrack.load()["weeks"]
    print("voltrack ok")


if __name__ == "__main__":
    main()
