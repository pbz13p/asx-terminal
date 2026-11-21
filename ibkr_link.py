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
import time

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


def _clean(x):
    import math
    if isinstance(x, float):
        return None if (math.isnan(x) or x < 0) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def status(code="CND"):
    try:
        return _clean(_status(code))
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
                ib.connect(HOST, port, clientId=17, timeout=12, readonly=True)
                out.update(connected=True, port=port, paper=port in (4002, 7497))
                break
            except Exception as e:
                err = (err + "; " if err else "") + f"{port}: {type(e).__name__} {str(e)[:80]}"
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


_qcache = {}


def quote(code):
    """Fast quote for the terminal's right panel: connect, one ASX quote, disconnect. Cached 20 s per code."""
    hit = _qcache.get(code)
    if hit and time.time() - hit[0] < 20:
        return hit[1]
    try:
        out = _clean(_quote(code))
    except Exception as e:
        out = {"connected": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    _qcache[code] = (time.time(), out)
    return out


def _quote(code):
    with _lock:
        asyncio.set_event_loop(asyncio.new_event_loop())
        ib_async = _lib()
        ib = ib_async.IB()
        port, errs = None, []
        for p in PORTS:
            for cid in (18, 28, 38):          # a dropped earlier session can still hold a client id for a while
                try:
                    ib.connect(HOST, p, clientId=cid, timeout=8, readonly=True)
                    port = p
                    break
                except ConnectionRefusedError:
                    errs.append(f"{p}: refused")
                    break
                except Exception as e:
                    errs.append(f"{p}/{cid}: {type(e).__name__} {str(e)[:60]}")
                    try:
                        ib.disconnect()
                    except Exception:
                        pass
            if port:
                break
        if port is None:
            return {"connected": False, "error": "IB Gateway 没打开或没登录", "detail": "; ".join(errs)[:400]}
        try:
            ib.reqMarketDataType(3)        # live if subscribed, otherwise delayed (free)
            c = ib_async.Stock(code, "ASX", "AUD")
            if not ib.qualifyContracts(c):
                return {"connected": True, "port": port, "paper": port in (4002, 7497), "error": "盈透找不到这只股票"}
            t = ib.reqMktData(c, "", False, False)
            for _ in range(12):
                ib.sleep(0.5)
                if t.last == t.last and t.volume == t.volume:
                    break
            out = {"connected": True, "port": port, "paper": port in (4002, 7497),
                   "type": {1: "实时", 2: "冻结", 3: "延迟约 20 分钟", 4: "延迟（收盘后）"}.get(t.marketDataType, str(t.marketDataType)),
                   "last": t.last, "bid": t.bid, "ask": t.ask, "bid_size": t.bidSize, "ask_size": t.askSize,
                   "close": t.close, "open": t.open, "high": t.high, "low": t.low, "volume": t.volume,
                   "time": t.time.isoformat() if getattr(t, "time", None) else None}
            ib.cancelMktData(c)
            return out
        finally:
            ib.disconnect()


def probe(codes=("CND", "BHP", "PLS", "WES", "LTR")):
    """What can this account pull through the API? Daily bars (TRADES, 60 days) per stock and the fundamental reports."""
    with _lock:
        asyncio.set_event_loop(asyncio.new_event_loop())
        ib_async = _lib()
        ib = ib_async.IB()
        errs = []
        ib.errorEvent += lambda reqId, code, msg, *a: errs.append(f"{code}: {msg[:140]}")
        try:
            ib.connect(HOST, 4002, clientId=19, timeout=12, readonly=True)
        except Exception as e:
            return {"connected": False, "error": f"{type(e).__name__}"}
        out = {"connected": True, "bars": {}, "fundamentals": {}}
        try:
            for code in codes:
                c = ib_async.Stock(code, "ASX", "AUD")
                if not ib.qualifyContracts(c):
                    out["bars"][code] = "not found"
                    continue
                for what in ("TRADES",):
                    try:
                        b = ib.reqHistoricalData(c, "", "3 M", "1 day", what, True, timeout=30)
                        out["bars"][code] = [[str(x.date), x.close, x.volume] for x in b[-15:]]
                    except Exception as e:
                        out["bars"][code] = f"{type(e).__name__}: {str(e)[:120]}"
                if code in ("BHP", "CND"):
                    try:
                        cd = ib.reqContractDetails(c)
                        if cd:
                            d = cd[0]
                            out["fundamentals"][code + "_details"] = {"longName": d.longName, "industry": d.industry, "category": d.category,
                                                                      "subcategory": d.subcategory, "validExchanges": d.validExchanges}
                    except Exception as e:
                        out["fundamentals"][code + "_details"] = type(e).__name__
                    for rep in ("ReportSnapshot", "ReportsFinStatements", "RESC"):
                        try:
                            x = ib.reqFundamentalData(c, rep)
                            out["fundamentals"][f"{code}_{rep}"] = (len(x or ""), (x or "")[:200])
                        except Exception as e:
                            out["fundamentals"][f"{code}_{rep}"] = f"{type(e).__name__}: {str(e)[:120]}"
            try:
                out["head_ts"] = str(ib.reqHeadTimeStamp(ib_async.Stock("BHP", "ASX", "AUD"), "TRADES", True, 1))
            except Exception as e:
                out["head_ts"] = type(e).__name__
        finally:
            out["errors"] = errs[-25:]
            ib.disconnect()
        return _clean(out)
