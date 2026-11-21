"""The contract between the screen and any data source.

The web page only ever sees these shapes. To add a data source, subclass
Provider, fill in the methods and set DATA_PROVIDER in .env.

quote : {code, last, prev, open, high, low, volume, time, spark: [closes]}
index : {code, label, last, prev, history: [closes]}
stock : {code, name, last, prev, volume}
candle: {t, o, h, l, c, v}
"""


class DataUnavailable(Exception):
    """The source cannot supply this (plan limits, unsupported, or down)."""


class Provider:
    name = "base"
    latency = "unknown"
    capabilities = {"quotes": False, "intraday": False, "sales": False, "book": False}

    def market(self):
        """Every listed share: {asof, stocks: [stock]}."""
        raise DataUnavailable

    def quotes(self, codes):
        """Latest quote for each code: [quote]."""
        raise DataUnavailable

    def indices(self):
        """Index and sector readings: {main: [index], sectors: [index]}."""
        raise DataUnavailable

    def weekly(self):
        """周报: this week's sector moves and leaders."""
        raise DataUnavailable("周报只在 EODHD 数据源下可用")

    def board(self):
        """行业页: sector and small-cap indices, small-cap mood, sector leaders."""
        raise DataUnavailable("行业页只在 EODHD 数据源下可用")

    def candles(self, code, tf):
        """tf is 1D or 5D (intraday) or a daily range (1M 6M 1Y 5Y ALL: full daily history is returned). Oldest first."""
        raise DataUnavailable

    def history(self, code, years=20):
        """Daily closes for a share or index code: [{date, close}], oldest first."""
        raise DataUnavailable

    def symbols(self):
        """Valid share codes as a set."""
        raise DataUnavailable

    def sales(self, code):
        """Trade-by-trade prints: [{t, p, v}], newest first."""
        raise DataUnavailable

    def book(self, code):
        """Depth: {bids: [[price, size]], asks: [[price, size]]}."""
        raise DataUnavailable
