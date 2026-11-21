"""Link to Interactive Brokers through IB Gateway on Frank's PC (2026-11-21: paper account first).

Gateway settings (checked 2026-11-21): read-only API, port 4002 (paper; live would be 4001), localhost only.
Read-only means this code cannot place orders even by mistake.

status(code): connects, reads the account summary, one quote (delayed if there is no live subscription) and the last
daily bars for an ASX stock, so we can see what the account is allowed to get.
"""
import asyncio
import subprocess
import sys
import threading

HOST, PORTS = "127.0.0.1", (4002, 4001, 7497, 7496)
_lock = threading.Lock()


def _lib():
    try:
        import ib_async  # noqa: F401
    except ImportError:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", "ib_async"], capture_output=True, text=True)
        import importlib
        import site
        site.addsitedir(site.getusersitepackages())
        importlib.invalidate_caches()
        if r.returncode:
            raise RuntimeError("pip: " + (r.stderr or r.stdout)[-300:])
    import ib_async
    return ib_async


def status(code="CND"):
    try:
        return _status(code)
    except Exception as e:
        import traceback
        return {"connected": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "trace": traceback.format_exc()[-800:]}


def _status(code):
    with _lock:
        asyncio.set_event_loop(asyncio.new_event_loop())
        ib_async = _lib()
        ib = ib_async.IB()
        out = {"connected": False}
        err = None
        for port in PORTS:
            try:
                ib.connect(HOST, port, clientId=17, timeout=8, readonly=True)
                out.update(connected=True, port=port, paper=port in (4002, 7497))
                break
            except Exception as e:
                err = f"{port}: {type(e).__name__}"
        if not out["connected"]:
            out["error"] = "连不上 IB Gateway（没打开或没登录？）" + (f" [{err}]" if err else "")
            return out
        try:
            out["accounts"] = ib.managedAccounts()
            summ = {v.tag: v.value for v in ib.accountSummary() if v.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower", "AccountType")}
            out["summary"] = summ
            ib.reqMarketDataType(3)            # 3 = delayed if no live permission (free); live once ASX data is subscribed
            c = ib_async.Stock(code, "ASX", "AUD")
            ib.qualifyContracts(c)
            t = ib.reqMktData(c, "", False, False)
            ib.sleep(4)
            out["quote"] = {"last": t.last, "bid": t.bid, "ask": t.ask, "close": t.close, "volume": t.volume,
                            "type": {1: "实时", 2: "冻结", 3: "延迟", 4: "延迟冻结"}.get(t.marketDataType, t.marketDataType)}
            ib.cancelMktData(c)
            bars = ib.reqHistoricalData(c, "", "10 D", "1 day", "TRADES", True, timeout=20)
            out["bars"] = [{"date": str(b.date), "close": b.close, "volume": b.volume} for b in bars[-5:]]
        except Exception as e:
            out["data_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            ib.disconnect()
        return out
