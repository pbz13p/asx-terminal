"""周报 (provider.weekly): week boundaries, liquidity filter, consolidation flag, sector ranking. No network."""
from datetime import date, timedelta

from providers.eodhd import EodhdProvider


class Fake(EodhdProvider):
    def __init__(self):
        super().__init__()
        self.key = "x"

    def _long(self, symbol):
        days = [date(2026, 11, 2) + timedelta(days=i) for i in range(19)]
        days = [d.isoformat() for d in days if d.weekday() < 5]          # 2026-11-02 .. 2026-11-20 (Fri)
        k = {"AXJO.INDX": 0.95, "AXMJ.INDX": 0.88, "AXUJ.INDX": 1.004}.get(symbol, 0.97)
        return [(d, 100.0 if d <= "2026-11-13" else 100.0 * k) for d in days]

    def market(self):
        return {"asof": "2026-11-20", "stocks": [
            {"code": "AAA", "name": "AAA LTD", "last": 2.0, "prev": 1.9, "volume": 500_000},    # +100%, raw base 0.1 -> consolidation flag
            {"code": "BBB", "name": "BBB LTD", "last": 1.1, "prev": 1.0, "volume": 1_000_000},  # +10%
            {"code": "CCC", "name": "CCC LTD", "last": 0.9, "prev": 1.0, "volume": 1_000_000},  # -10%
            {"code": "ILL", "name": "ILLIQUID", "last": 3.0, "prev": 1.0, "volume": 1_000},     # filtered out
            {"code": "UUU", "name": "UTIL", "last": 5.0, "prev": 5.0, "volume": 100_000}]}

    def _bulk(self, day=None):
        assert day == "2026-11-13"
        return [{"code": "AAA", "close": 0.1, "adjusted_close": 1.0}, {"code": "BBB", "close": 1.0},
                {"code": "CCC", "close": 1.0}, {"code": "ILL", "close": 1.0}, {"code": "UUU", "close": 4.9}]

    def _sector_map(self, codes):
        return {"AAA": "Materials", "BBB": "Materials", "CCC": "Materials", "ILL": "Materials", "UUU": "Utilities"}


def test_weekly():
    W = Fake().weekly()
    assert W["base_day"] == "2026-11-13" and W["week_from"] == "2026-11-16" and W["complete"] and W["days"] == 5
    assert [r["code"] for r in W["index"]][0] == "XJO" and W["index"][0]["w"] == -5.0
    sec = {r["code"]: r for r in W["sectors"]}
    assert W["sectors"][0]["code"] == "XUJ" and W["sectors"][-1]["code"] == "XMJ"
    top = sec["XMJ"]["top"]
    assert [x["code"] for x in top] == ["AAA", "BBB", "CCC"] and top[0]["w"] == 100.0 and "合股" in top[0]["check"]
    assert sec["XMJ"]["n"] == 3 and sec["XMJ"]["up_pct"] == 67
    assert W["breadth"]["n"] == 4


if __name__ == "__main__":
    test_weekly()
    print("ok")
