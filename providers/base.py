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

    def candles(self, code, tf):
        """tf is one of 1D 5D 1M 6M 1Y. Returns [candle], oldest first."""
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
