"""Interactive Brokers source. Reserved until the account is open.

Needs IB Gateway running on this computer and an ASX Total subscription.
Market data from this source is for your own use only.
"""
from .base import DataUnavailable, Provider


class IbkrProvider(Provider):
    name = "ibkr"
    latency = "实时"
    capabilities = {"quotes": True, "intraday": True, "sales": True, "book": True}

    def __init__(self):
        raise DataUnavailable("IBKR source is not connected yet")
