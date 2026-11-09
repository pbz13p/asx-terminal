"""Pick the data source named by DATA_PROVIDER in .env."""
import os


def get_provider():
    name = os.environ.get("DATA_PROVIDER", "eodhd").strip().lower()
    if name == "eodhd":
        from .eodhd import EodhdProvider
        return EodhdProvider()
    if name == "ibkr":
        from .ibkr import IbkrProvider
        return IbkrProvider()
    raise SystemExit(f"Unknown DATA_PROVIDER: {name}")
