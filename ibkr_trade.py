"""Order ticket for the terminal (Frank 2026-11-25: "在我自己的 terminal 上下单"). Frank's PC only, through his own TWS.

Safety rules (fixed with Frank 2026-11-25):
  * Limit orders only (no market orders), day orders, regular hours; ASX (AUD) and US (USD, SMART routing).
  * Every order is previewed first: IBKR's own what-if check (commission, cash after), current bid/ask, how many ticks
    away, warnings. The ticket returns a one-time token; placing needs that token within 120 s and the exact same order.
  * Caps from local/ibkr_settings.json: max_order_value (0 = none; Frank 2026-11-25 set none -- the account's cash is
    the limit) and max_orders_day (20).
  * Live trading is OFF until Frank turns it on in ⚙ 设置 (trade_live = true); until then only a paper TWS / Gateway
    (ports 7497 / 4002) accepts orders. TWS must also have "Read-Only API" unticked -- that switch is Frank's.
  * Every preview / order / cancel is appended to local/orders_log.json (on this PC only, never in git).
  * The browser must send the X-Neon header from the terminal's own page (blocks other web pages from posting here).
"""
import asyncio
import hashlib
import json
import math
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / "local"
LOG = LOCAL / "orders_log.json"
HOST = "127.0.0.1"
LIVE_PORTS, PAPER_PORTS = (7496, 4001), (7497, 4002)
CLIENT_ID = 51
_lock = threading.Lock()
_tokens = {}


def settings():
    try:
        return json.loads((LOCAL / "ibkr_settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(**kw):
    s = settings()
    s.update(kw)
    LOCAL.mkdir(exist_ok=True)
    (LOCAL / "ibkr_settings.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def log(entry):
    LOCAL.mkdir(exist_ok=True)
    try:
        rows = json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:
        rows = []
    rows.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **entry})
    LOG.write_text(json.dumps(rows, ensure_ascii=False, indent=0), encoding="utf-8")


def _num(x):
    """IBKR sends -1 / NaN when there is no price (market closed, no data)."""
    if not isinstance(x, (int, float)) or isinstance(x, bool) or (isinstance(x, float) and math.isnan(x)) or x <= 0 or x > 1e15:
        return None
    return x


def _float(x):
    try:
        v = float(x)
        return v if abs(v) < 1e15 else None
    except (TypeError, ValueError):
        return None


def tick(price, mkt):
    if mkt == "us":
        return 0.0001 if price < 1 else 0.01
    return 0.001 if price < 0.10 else 0.005 if price < 2 else 0.01


def check(order):
    """Validate a ticket from the browser; returns the cleaned order or raises ValueError (Chinese message)."""
    mkt = order.get("mkt")
    if mkt not in ("au", "us"):
        raise ValueError("只支持澳股和美股")
    code = str(order.get("code") or "").upper().strip()
    if not code or len(code) > 10 or not all(ch.isalnum() or ch in ".-" for ch in code):
        raise ValueError("代码不正确")
    side = order.get("side")
    if side not in ("BUY", "SELL"):
        raise ValueError("请选择买入还是卖出")
    try:
        qty = int(order.get("qty"))
        price = float(order.get("price"))
    except (TypeError, ValueError):
        raise ValueError("数量和价格要填数字") from None
    if qty <= 0 or qty > 1_000_000_000:   # penny stocks trade in the tens of millions
        raise ValueError("数量不正确")
    if not price > 0:
        raise ValueError("限价要大于 0")
    t = tick(price, mkt)
    if abs(round(price / t) * t - price) > t / 1000:
        raise ValueError(f"价格要按最小价位 {t:g} 的整数倍填")
    value = qty * price
    cap = settings().get("max_order_value", 1000)
    cap = float(cap) if cap not in (None, "") else 0.0          # 0 = no cap (Frank 2026-11-25: 账户里的钱就是上限)
    if cap > 0 and value > cap + 1e-9:
        raise ValueError(f"这笔 {value:,.2f} 超过单笔上限 {cap:,.0f}（在 ⚙ 设置里改）")
    return {"mkt": mkt, "code": code, "side": side, "qty": qty, "price": round(price, 6), "value": round(value, 2),
            "ccy": "USD" if mkt == "us" else "AUD"}


def _connect(ib_async, placing=False):
    """Preview, account view and cancel may use the live TWS; placing a new order needs trade_live (or a paper TWS)."""
    ib = ib_async.IB()
    ib.RequestTimeout = 20          # never hang the terminal on a TWS request (a hung call held the lock on 2026-11-25)
    live_ok = bool(settings().get("trade_live")) or not placing
    for port in (LIVE_PORTS if live_ok else ()) + PAPER_PORTS:
        try:
            ib.connect(HOST, port, clientId=CLIENT_ID, timeout=10, readonly=False)
            return ib, port
        except Exception:
            try:
                ib.disconnect()
            except Exception:
                pass
    if not live_ok:
        raise RuntimeError("真账户下单还没打开（⚙ 设置里打开）；现在只能预览，不会真的下单")
    raise RuntimeError("连不上 TWS：TWS 没打开或没登录")


def _contract(ib_async, o):
    if o["mkt"] == "us":
        return ib_async.Stock(o["code"].replace("-", " "), "SMART", "USD")
    return ib_async.Stock(o["code"], "ASX", "AUD")


def _run(fn, placing=False):
    from ibkr_link import _lib
    if not _lock.acquire(timeout=45):
        raise RuntimeError("盈透正忙（上一笔请求还没结束），过一会儿再试")
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
        ib_async = _lib()
        ib, port = _connect(ib_async, placing)
        try:
            return fn(ib_async, ib, port)
        finally:
            ib.disconnect()
    finally:
        _lock.release()


def _token(o):
    raw = json.dumps([o[k] for k in ("mkt", "code", "side", "qty", "price")]) + str(time.time())
    tok = hashlib.sha256(raw.encode()).hexdigest()[:24]
    _tokens[tok] = (time.time(), {k: o[k] for k in ("mkt", "code", "side", "qty", "price")})
    return tok


def preview(order):
    o = check(order)

    def go(ib_async, ib, port):
        c = _contract(ib_async, o)
        if not ib.qualifyContracts(c):
            raise RuntimeError("盈透找不到这只股票")
        ib.reqMarketDataType(3)
        t = ib.reqMktData(c, "", False, False)
        for _ in range(8):
            ib.sleep(0.4)
            if _num(t.bid) or _num(t.ask) or _num(t.last):
                break
        bid, ask, last = _num(t.bid), _num(t.ask), _num(t.last) or _num(t.close)
        ib.cancelMktData(c)
        lo = ib_async.LimitOrder(o["side"], o["qty"], o["price"], tif="DAY", outsideRth=False)
        warn = []
        try:
            st = ib.whatIfOrder(c, lo)
        except Exception:
            st = None
        if not st:
            warn.append("盈透没给出佣金预估（TWS 只读模式时会这样）")
        comm = _num(getattr(st, "commission", None)) if st else None
        ref = ask if o["side"] == "BUY" else bid
        tk = tick(o["price"], o["mkt"])
        if ref:
            away = (o["price"] - ref) / tk * (1 if o["side"] == "BUY" else -1)
            if away > 2:
                warn.append(f"限价比{'卖一' if o['side'] == 'BUY' else '买一'}{'高' if o['side'] == 'BUY' else '低'} {away:.0f} 格，会马上成交，可能多付了")
            if away < -20:
                warn.append(f"限价离{'卖一' if o['side'] == 'BUY' else '买一'}差 {-away:.0f} 格，今天大概率成交不了")
        if bid and ask and bid > 0:
            sp = (ask - bid) / ((ask + bid) / 2) * 100
            if sp > 2:
                warn.append(f"买卖价差 {sp:.1f}%，一进一出先亏这么多")
        if comm and o["value"] and comm / o["value"] > 0.01:
            warn.append(f"佣金占这笔的 {100 * comm / o['value']:.1f}%")
        if getattr(st, "warningText", ""):
            warn.append(str(st.warningText)[:200])
        out = {**o, "paper": port in PAPER_PORTS, "bid": bid, "ask": ask, "last": last, "commission": comm,
               "commission_ccy": getattr(st, "commissionCurrency", "") if st else "",
               "cash_after": _float(getattr(st, "equityWithLoanAfter", None)) if st else None,
               "warnings": warn, "token": _token(o)}
        log({"action": "preview", **{k: out[k] for k in ("mkt", "code", "side", "qty", "price", "value", "paper")}})
        return out
    return _run(go)


def place(order, token):
    o = check(order)
    hit = _tokens.pop(token or "", None)
    if not hit or time.time() - hit[0] > 120 or hit[1] != {k: o[k] for k in ("mkt", "code", "side", "qty", "price")}:
        raise ValueError("确认已过期或订单改过了，请重新预览")
    s = settings()
    today = time.strftime("%Y-%m-%d")
    try:
        n_today = sum(1 for r in json.loads(LOG.read_text(encoding="utf-8")) if r.get("action") == "place" and r.get("time", "").startswith(today))
    except Exception:
        n_today = 0
    if n_today >= int(s.get("max_orders_day") or 20):
        raise ValueError("今天下单次数到上限了（⚙ 设置里改）")

    def go(ib_async, ib, port):
        c = _contract(ib_async, o)
        if not ib.qualifyContracts(c):
            raise RuntimeError("盈透找不到这只股票")
        lo = ib_async.LimitOrder(o["side"], o["qty"], o["price"], tif="DAY", outsideRth=False)
        lo.orderRef = "NeonTerminal"
        errs = []
        ib.errorEvent += lambda reqId, code, msg, *a: errs.append((code, msg))
        tr = ib.placeOrder(c, lo)
        for _ in range(12):
            ib.sleep(0.5)
            if tr.orderStatus.status not in ("PendingSubmit", "") or errs:
                break
        ro = [m for code, m in errs if code == 321 or "read-only" in m.lower() or "read only" in m.lower()]
        if ro:
            raise RuntimeError("TWS 现在是只读模式：在 TWS 的 API 设置里去掉 Read-Only API 的勾才能下单")
        out = {**o, "paper": port in PAPER_PORTS, "order_id": tr.order.orderId, "status": tr.orderStatus.status,
               "errors": [f"{code}: {m}"[:200] for code, m in errs if code not in (2104, 2106, 2158, 2107, 2108)]}
        log({"action": "place", **{k: out[k] for k in ("mkt", "code", "side", "qty", "price", "value", "paper", "order_id", "status")}})
        return out
    return _run(go, placing=True)


def cancel(order_id):
    oid = int(order_id)

    def go(ib_async, ib, port):
        ib.reqAllOpenOrders()
        ib.sleep(1)
        for tr in ib.openTrades():
            if tr.order.orderId == oid or tr.order.permId == oid:
                ib.cancelOrder(tr.order)
                ib.sleep(1)
                log({"action": "cancel", "order_id": oid, "code": tr.contract.symbol})
                return {"cancelled": oid, "status": tr.orderStatus.status}
        raise RuntimeError("找不到这笔挂单（可能已成交或已撤）")
    return _run(go)


def overview():
    """Cash, positions and open orders for the panel."""
    def go(ib_async, ib, port):
        summ = {v.tag + ":" + v.currency: v.value for v in ib.accountSummary()
                if v.tag in ("NetLiquidation", "TotalCashValue", "AvailableFunds", "BuyingPower")}
        pos = [{"code": p.contract.symbol.replace(" ", "-"), "exch": p.contract.primaryExchange or p.contract.exchange,
                "ccy": p.contract.currency, "qty": p.position, "avg": round(p.avgCost, 6)} for p in ib.positions()]
        ib.reqAllOpenOrders()
        ib.sleep(1)
        orders = [{"id": tr.order.permId or tr.order.orderId, "code": tr.contract.symbol.replace(" ", "-"), "ccy": tr.contract.currency,
                   "side": tr.order.action, "qty": tr.order.totalQuantity, "price": tr.order.lmtPrice,
                   "status": tr.orderStatus.status, "filled": tr.orderStatus.filled}
                  for tr in ib.openTrades() if tr.orderStatus.status not in ("Cancelled", "Filled", "Inactive")]
        return {"paper": port in PAPER_PORTS, "port": port, "summary": summ, "positions": pos, "orders": orders,
                "live_on": bool(settings().get("trade_live")), "max_order_value": float(settings().get("max_order_value", 1000) or 0)}
    return _run(go)
