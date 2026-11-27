"""Crypto perps through Hyperliquid (Frank 2026-11-27: "接吧" -- 直接在 Neon 上买比特币、可加杠杆).

Hyperliquid is open to Australian residents (restricted: US, Ontario, sanctioned places), no KYC, unregulated:
no negative-balance protection, no complaints body. Frank chose it knowingly; these guard rails are ours:

  * Keys: only an API ("agent") wallet key is stored, in local/hl_creds.json on Frank's PC (typed by Frank in
    ⚙ 设置). An agent wallet can trade but cannot withdraw. The main wallet's seed phrase never comes near the terminal.
  * Isolated margin per position, so one trade can only lose the margin put into it.
  * Leverage cap: default 5x (hl_max_lev). Raising it needs the confirm sentence in ⚙ 设置; never above the
    exchange's own max for the coin.
  * Above 3x a stop is required; the stop is placed right after the fill as a reduce-only stop-market order.
    If the exchange rejects the stop, the position is closed at once (no naked leveraged position).
  * One trade may use at most hl_max_frac (default 30%) of the account value as margin.
  * Preview first (price, notional, margin, liquidation price, loss at the stop, fees); placing needs the
    one-time token within 120 s and the identical order. Daily order cap shared with the IBKR ticket.
  * Every preview / order / stop / close goes into local/orders_log.json (this PC only).
"""
import hashlib
import json
import math
import subprocess
import sys
import threading
import time

import ibkr_trade as T

CREDS = T.LOCAL / "hl_creds.json"
TAKER = 0.00045          # Hyperliquid base taker fee per side (tier 0); preview adds it twice
_lock = threading.Lock()
_tokens = {}


def _lib():
    try:
        import hyperliquid  # noqa: F401
    except ImportError:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", "hyperliquid-python-sdk"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode:
            raise RuntimeError("装 Hyperliquid 组件失败：" + (r.stderr or r.stdout)[-200:])
        import site
        import importlib
        sys.path.append(site.getusersitepackages())
        importlib.invalidate_caches()
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    import eth_account
    return Exchange, Info, constants, eth_account


def creds():
    try:
        c = json.loads(CREDS.read_text(encoding="utf-8"))
    except Exception:
        c = {}
    if not (c.get("address") and c.get("key")):
        raise RuntimeError("还没接上 Hyperliquid：点右上角 ⚙ 设置，在「Hyperliquid」一栏填主钱包地址和 API 钱包私钥（见「待 Frank 处理」F8）")
    return c


def limits():
    s = T.settings()
    return {"max_lev": int(s.get("hl_max_lev") or 5), "max_frac": float(s.get("hl_max_frac") or 0.3)}


def _clients(trading=False):
    Exchange, Info, constants, eth_account = _lib()
    c = creds()
    url = constants.TESTNET_API_URL if c.get("net") == "testnet" else constants.MAINNET_API_URL
    info = Info(url, skip_ws=True, timeout=20)
    if not trading:
        return info, None, c
    wallet = eth_account.Account.from_key(c["key"])
    ex = Exchange(wallet, url, account_address=c["address"], timeout=20)
    return info, ex, c


def _asset(info, coin):
    meta = info.meta()
    a = next((u for u in meta["universe"] if u["name"] == coin and not u.get("isDelisted")), None)
    if not a:
        raise RuntimeError(f"Hyperliquid 上没有 {coin} 的永续合约")
    return a


def _px(p, sz_dec):
    """Hyperliquid price rule: at most 5 significant figures and at most 6 - szDecimals decimals."""
    if p <= 0:
        return p
    p = float(f"{p:.5g}")
    return round(p, max(0, 6 - sz_dec))


_RISK = {"t": 0, "tab": None}


def risk_table():
    """Round 104 liquidation table (crypto_risk.py, weekly, encrypted on the quick-results branch)."""
    if _RISK["tab"] is not None and time.time() - _RISK["t"] < 6 * 3600:
        return _RISK["tab"]
    import urllib.request
    import os
    tab = None
    try:
        with urllib.request.urlopen("https://raw.githubusercontent.com/pbz13p/asx-terminal/quick-results/crypto_risk.json.enc", timeout=20) as r:
            raw = r.read()
        if os.environ.get("ASX_DATA_KEY"):
            import securedata
            tab = json.loads(securedata.decrypt(raw))
    except Exception:
        tab = None
    _RISK.update(t=time.time(), tab=tab)
    return tab


def risk_line(code, side, lev, stop_dist, px, hold_days, margin):
    """'按 2019 年起历史 …' for the preview. Buckets: leverage 1/2/3/5 (4 -> 5, more conservative), stop 2/5/10% or none."""
    tab = risk_table()
    sym = code.upper() + "USDT"
    if not tab:
        return {"text": "历史爆仓概率表还没生成（第 104 轮跑完后自动出现）"}
    if sym not in tab.get("symbols", {}):
        return {"text": f"历史爆仓概率表只有 BTC、ETH；{code} 没有"}
    levs = [L for L in (1, 2, 3, 5) if L >= lev]
    if not levs:
        return {"text": f"{lev} 倍超出历史表（最多 5 倍）"}
    L = levs[0]
    sp = 0
    if stop_dist and px:
        pct = stop_dist / px * 100
        sp = min((2, 5, 10), key=lambda b: abs(b - pct))
    days = hold_days if hold_days in (1, 3, 7, 30) else 7
    cell = tab["symbols"][sym]["table"].get(f"{'long' if side == 'BUY' else 'short'}|{L}|{sp}|{days}")
    if not cell:
        return {"text": "历史表里没有这个组合"}
    meta = tab["symbols"][sym]["meta"]
    out = {"lev_used": L, "stop_used": sp, "days": days, "stop_pct": cell["stop"], "liq_pct": cell["liq"],
           "mean": cell["mean"], "win": cell["win"], "worst": cell["worst"], "worst_when": cell.get("worst_when"),
           "from": meta["from"], "red": cell["liq"] >= 5}
    txt = (f"按 {meta['from'][:4]} 年起历史（{code} {('做多' if side == 'BUY' else '做空')} {L} 倍、"
           f"{'止损约 ' + str(sp) + '%' if sp else '不设止损'}、持有 {days} 天）：" + (f"{cell['stop']}% 先碰止损，" if sp else "") + f"{cell['liq']}% 先爆仓；"
           f"到期平均 {cell['mean']:+.1f}%（占保证金），赚钱的占 {cell['win']}%")
    if L != lev:
        txt += f"（表里没有 {lev} 倍，按 {L} 倍算）"
    if not sp and lev > 3:
        worst_money = margin * cell["worst"] / 100 if margin else None
        txt += f"。历史最坏一次：亏保证金的 {abs(cell['worst']):.0f}%" + (f"（这笔就是 {abs(worst_money):,.0f} 美元）" if worst_money else "") + \
               (f"，发生在 {cell.get('worst_when')}" if cell.get("worst_when") else "")
    out["text"] = txt
    return out


def check(order, strict=True):
    o = {"code": str(order.get("code", "")).upper().replace("USDT", "").replace("-PERP", "")}
    if not o["code"].isalnum() or len(o["code"]) > 12:
        raise ValueError("代码不正确")
    o["side"] = str(order.get("side", "")).upper()
    if o["side"] not in ("BUY", "SELL"):
        raise ValueError("方向只能是做多或做空")
    try:
        o["qty"] = float(order.get("qty"))
        o["lev"] = int(float(order.get("lev") or 1))
    except (TypeError, ValueError):
        raise ValueError("数量、杠杆要填数字") from None
    if not 0 < o["qty"] < 1e9:
        raise ValueError("数量不对")
    lim = limits()
    if not 1 <= o["lev"] <= lim["max_lev"]:
        raise ValueError(f"杠杆要在 1 到 {lim['max_lev']} 倍之间（上限在 ⚙ 设置里改）")
    stop = order.get("stop")
    try:
        o["stop"] = float(stop) if stop not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        raise ValueError("止损距离要填数字") from None
    if o["stop"] is not None and o["stop"] <= 0:
        raise ValueError("止损距离要大于 0")
    if o["lev"] > 3 and not o["stop"]:
        if strict:
            raise ValueError("杠杆超过 3 倍必须填止损距离")
        o["blocked"] = "杠杆超过 3 倍必须设止损：这笔不能下单，下面只给你看历史风险"
    return o


def _tok(o):
    t = hashlib.sha256((json.dumps(o, sort_keys=True) + str(time.time())).encode()).hexdigest()[:24]
    _tokens[t] = (time.time(), dict(o))
    return t


def preview(order):
    o = check(order, strict=False)
    try:
        info, _, c = _clients()
    except RuntimeError as e:                # not connected yet: public prices + history only, no order possible
        Exchange, Info, constants, eth_account = _lib()
        info, c = Info(constants.MAINNET_API_URL, skip_ws=True, timeout=20), None
        o["blocked"] = "；".join(x for x in (o.get("blocked"), str(e)) if x)
    a = _asset(info, o["code"])
    mid = float(info.all_mids().get(o["code"]) or 0)
    if not mid:
        raise RuntimeError("Hyperliquid 现在没有报价")
    if o["lev"] > int(a.get("maxLeverage", 1)):
        raise ValueError(f"{o['code']} 在 Hyperliquid 最多 {a['maxLeverage']} 倍")
    sz = round(o["qty"], int(a["szDecimals"]))
    if sz <= 0:
        raise ValueError(f"数量太小：{o['code']} 最小单位是 {10 ** -int(a['szDecimals'])}")
    st = info.user_state(c["address"]) if c else {}
    acct = float((st.get("marginSummary") or {}).get("accountValue") or 0)
    free = float(st.get("withdrawable") or 0) if c else None
    notional = sz * mid
    margin = notional / o["lev"]
    mm = 1 / (2 * float(a["maxLeverage"]))                 # maintenance margin rate = half of max-leverage margin
    long = o["side"] == "BUY"
    liq = mid * (1 - 1 / o["lev"] + mm) if long else mid * (1 + 1 / o["lev"] - mm)
    fees = notional * TAKER * 2
    stop_px = (mid - o["stop"]) if (o["stop"] and long) else (mid + o["stop"]) if o["stop"] else None
    loss = sz * o["stop"] + fees if o["stop"] else None
    lim = limits()
    warn = []
    if c and acct and margin > lim["max_frac"] * acct:
        o.setdefault("blocked", f"这笔要用保证金 {margin:,.0f} 美元，超过账户的 {lim['max_frac']:.0%}（{lim['max_frac'] * acct:,.0f}）；少买点或在 ⚙ 设置里改")
    if c and margin > free:
        o.setdefault("blocked", f"可用资金只有 {free:,.2f} 美元，这笔要 {margin:,.2f}")
    if stop_px is not None and ((long and stop_px <= liq) or (not long and stop_px >= liq)):
        warn.append("止损价在爆仓价外面，会先爆仓：把止损距离缩小，或者降杠杆")
    if not o["stop"]:
        warn.append("没设止损：跌到爆仓价会被强平，保证金全没")
    out = {**o, "qty": sz, "net": (c or {}).get("net", "mainnet"), "mid": mid, "notional": round(notional, 2), "margin": round(margin, 2),
           "liq": _px(liq, int(a["szDecimals"])), "stop_px": _px(stop_px, int(a["szDecimals"])) if stop_px else None,
           "loss_at_stop": round(loss, 2) if loss else None, "fees": round(fees, 2), "account": round(acct, 2),
           "free": round(free, 2) if free is not None else None, "max_lev_coin": a["maxLeverage"], "warnings": warn}
    try:
        hd = int(order.get("hold") or 7)
    except (TypeError, ValueError):
        hd = 7
    out["risk"] = risk_line(o["code"], o["side"], o["lev"], o["stop"], mid, hd, margin)
    out["token"] = None if o.get("blocked") else _tok({k: out[k] for k in ("code", "side", "qty", "lev", "stop")})
    T.log({"action": "preview", "broker": "HL", **{k: out[k] for k in ("code", "side", "qty", "lev", "stop", "net", "notional")}})
    return out


def _status(res):
    try:
        return res["response"]["data"]["statuses"][0]
    except Exception:
        return {"error": str(res)[:200]}


def place(order, token):
    o = check(order)
    hit = _tokens.pop(token or "", None)
    want = {k: o[k] for k in ("code", "side", "lev", "stop")}
    if not hit or time.time() - hit[0] > 120 or {k: hit[1][k] for k in want} != want or round(hit[1]["qty"], 8) != round(o["qty"], 8):
        raise ValueError("确认已过期或订单改过了，请重新预览")
    s = T.settings()
    today = time.strftime("%Y-%m-%d")
    try:
        n_today = sum(1 for r in json.loads(T.LOG.read_text(encoding="utf-8")) if r.get("action") == "place" and r.get("time", "").startswith(today))
    except Exception:
        n_today = 0
    if n_today >= int(s.get("max_orders_day") or 20):
        raise ValueError("今天下单次数到上限了（⚙ 设置里改）")
    with _lock:
        info, ex, c = _clients(trading=True)
        a = _asset(info, o["code"])
        sz = round(hit[1]["qty"], int(a["szDecimals"]))
        long = o["side"] == "BUY"
        lv = ex.update_leverage(o["lev"], o["code"], is_cross=False)
        if lv.get("status") != "ok":
            raise RuntimeError(f"设置杠杆失败：{str(lv)[:160]}")
        res = ex.market_open(o["code"], long, sz, slippage=0.01)
        stt = _status(res)
        if "filled" not in stt:
            T.log({"action": "place", "broker": "HL", "code": o["code"], "side": o["side"], "qty": sz, "status": "rejected", "why": str(stt)[:200]})
            raise RuntimeError(f"没成交：{stt.get('error') or stt}")
        fill = stt["filled"]
        avg = float(fill["avgPx"])
        out = {**o, "qty": float(fill["totalSz"]), "avg": avg, "oid": fill.get("oid"), "net": c.get("net", "mainnet"), "stop_set": None}
        if o["stop"]:
            trig = _px(avg - o["stop"] if long else avg + o["stop"], int(a["szDecimals"]))
            lim = _px(trig * (0.9 if long else 1.1), int(a["szDecimals"]))
            sr = ex.order(o["code"], not long, out["qty"], lim, {"trigger": {"triggerPx": trig, "isMarket": True, "tpsl": "sl"}}, reduce_only=True)
            ss = _status(sr)
            if "error" in ss:
                ex.market_close(o["code"])           # rule: no leveraged position without its stop
                T.log({"action": "place", "broker": "HL", **{k: out[k] for k in ("code", "side", "qty", "lev", "avg")}, "status": "closed: stop rejected", "why": str(ss)[:200]})
                raise RuntimeError(f"成交了但止损单被拒（{ss['error']}），已经马上平仓")
            out["stop_set"] = trig
        T.log({"action": "place", "broker": "HL", **{k: out[k] for k in ("code", "side", "qty", "lev", "stop", "avg", "stop_set", "net")}, "status": "filled"})
        return out


def close(coin):
    coin = str(coin).upper()
    with _lock:
        info, ex, c = _clients(trading=True)
        for od in info.open_orders(c["address"]):
            if od.get("coin") == coin:
                try:
                    ex.cancel(coin, od["oid"])
                except Exception:
                    pass
        res = ex.market_close(coin)
        stt = _status(res) if res else {"error": "没有这个持仓"}
        T.log({"action": "close", "broker": "HL", "code": coin, "result": str(stt)[:160]})
        if "error" in stt:
            raise RuntimeError(f"平仓失败：{stt['error']}")
        return {"closed": coin, "avg": (stt.get("filled") or {}).get("avgPx")}


def overview():
    info, _, c = _clients()
    st = info.user_state(c["address"])
    ms = st.get("marginSummary") or {}
    mids = info.all_mids()
    stops = {}
    for od in info.open_orders(c["address"]):
        stops.setdefault(od.get("coin"), []).append(od)
    rows = []
    for ap in st.get("assetPositions", []):
        p = ap.get("position") or {}
        szi = float(p.get("szi") or 0)
        if not szi:
            continue
        coin = p.get("coin")
        rows.append({"coin": coin, "side": "BUY" if szi > 0 else "SELL", "size": abs(szi), "entry": float(p.get("entryPx") or 0),
                     "now": float(mids.get(coin) or 0), "lev": (p.get("leverage") or {}).get("value"),
                     "liq": float(p["liquidationPx"]) if p.get("liquidationPx") else None,
                     "pnl": float(p.get("unrealizedPnl") or 0), "margin": float(p.get("marginUsed") or 0),
                     "orders": len(stops.get(coin, []))})
    lim = limits()
    return {"broker": "HL", "net": c.get("net", "mainnet"), "address": c["address"][:6] + "…" + c["address"][-4:],
            "account": float(ms.get("accountValue") or 0), "free": float(st.get("withdrawable") or 0),
            "positions": rows, "max_lev": lim["max_lev"], "max_frac": lim["max_frac"]}


def save_creds(address, key, net):
    """From ⚙ 设置 (Frank pastes them). Empty fields keep the old value. Then read the account once to test."""
    try:
        cur = json.loads(CREDS.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    address = (address or "").strip()
    key = (key or "").strip()
    if address:
        if not (address.startswith("0x") and len(address) == 42):
            raise ValueError("主钱包地址应该是 0x 开头、42 位")
        cur["address"] = address
    if key:
        k = key if key.startswith("0x") else "0x" + key
        if len(k) != 66:
            raise ValueError("API 钱包私钥应该是 0x 开头、66 位")
        cur["key"] = k
    if net in ("mainnet", "testnet"):
        cur["net"] = net
    T.LOCAL.mkdir(exist_ok=True)
    CREDS.write_text(json.dumps(cur), encoding="utf-8")
    T.log({"action": "settings", "broker": "HL", "net": cur.get("net", "mainnet")})
    ov = overview()
    _, _, constants, eth_account = _lib()
    agent = eth_account.Account.from_key(cur["key"]).address
    return {"net": ov["net"], "account": ov["account"], "address": ov["address"],
            "agent": agent[:6] + "…" + agent[-4:], "agent_is_main": agent.lower() == cur["address"].lower()}


def save_limits(max_lev=None, max_frac=None, confirm=""):
    kw = {}
    if max_lev is not None:
        v = max(1, min(50, int(max_lev)))
        if v > 5 and confirm != "我知道高杠杆会很快爆仓":
            raise ValueError("杠杆上限超过 5 倍，请输入确认语：我知道高杠杆会很快爆仓")
        kw["hl_max_lev"] = v
    if max_frac is not None:
        kw["hl_max_frac"] = max(0.01, min(1.0, float(max_frac) / 100))
    T.save_settings(**kw)
    T.log({"action": "settings", "broker": "HL", **kw})
    return limits()


_PRE = {"state": None}


def _preinstall():
    try:
        _lib()
        _PRE["state"] = "ok"
    except Exception as e:
        _PRE["state"] = "装组件失败：" + str(e)[:150]


def creds_status():
    if _PRE["state"] is None:          # install the SDK in the background the first time ⚙ 设置 opens
        _PRE["state"] = "installing"
        threading.Thread(target=_preinstall, daemon=True).start()
    try:
        c = creds()
        return {"set": True, "net": c.get("net", "mainnet"), "address": c["address"][:6] + "…" + c["address"][-4:], "sdk": _PRE["state"], **limits()}
    except Exception:
        return {"set": False, "sdk": _PRE["state"], **limits()}
