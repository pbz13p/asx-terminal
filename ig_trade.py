"""Crypto order ticket through IG (Frank 2026-11-27: "要在我自己的 Neon 上面能下单"，直接买比特币、能加杠杆).

Why IG: Frank's IBKR Australia account cannot trade spot crypto; Australian retail crypto leverage is capped at 2:1
(ASIC CFD order), IG is an ASIC-licensed CFD provider with a REST API (labs.ig.com) and a free demo account.

Safety rules (same spirit as ibkr_trade.py):
  * Credentials live only in the terminal's .env on Frank's PC, typed by Frank himself:
        IG_API_KEY=...   IG_USERNAME=...   IG_PASSWORD=...   IG_ENV=demo   (demo or live)
    Claude never sees or handles them. .env is git-ignored.
  * Demo first: IG_ENV=demo talks to demo-api.ig.com (virtual money). Live needs IG_ENV=live in .env AND
    "真账户下单" switched on in ⚙ 设置 with the confirm sentence (shared with the IBKR ticket).
  * Every order is previewed first (price, spread, size, notional, margin IG asks for, leverage, loss at the stop);
    placing needs the one-time token within 120 s and the exact same order. Market orders with an optional stop.
  * Daily order cap shared with the IBKR ticket (max_orders_day). Every preview / order / close goes into
    local/orders_log.json (this PC only).
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import ibkr_trade as T   # shared settings, log, token store, daily cap

ROOT = Path(__file__).resolve().parent
EPICS = T.LOCAL / "ig_epics.json"
NAMES = {"BTC": "Bitcoin", "ETH": "Ether", "SOL": "Solana", "XRP": "Ripple", "DOGE": "Dogecoin", "ADA": "Cardano",
         "LTC": "Litecoin", "BCH": "Bitcoin Cash", "LINK": "Chainlink", "DOT": "Polkadot", "AVAX": "Avalanche"}
_S = {"cst": None, "xst": None, "t": 0, "env": None, "acc": None}


def env():
    """Read .env fresh every time so Frank can fill it in without restarting the terminal."""
    vals = {}
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    try:   # ⚙ 设置 → IG 一栏保存在这里（只在这台电脑上，local/ 不进 git）
        vals.update({k: v for k, v in json.loads((T.LOCAL / "ig_creds.json").read_text(encoding="utf-8")).items() if v})
    except Exception:
        pass
    get = lambda k: vals.get(k) or os.environ.get(k) or ""
    e = {"key": get("IG_API_KEY"), "user": get("IG_USERNAME"), "pw": get("IG_PASSWORD"),
         "env": (get("IG_ENV") or "demo").lower(), "acc": get("IG_ACCOUNT_ID")}
    if not (e["key"] and e["user"] and e["pw"]):
        raise RuntimeError("还没接上 IG：点右上角 ⚙ 设置，在「IG（加密下单）」一栏填 API Key、用户名、密码（见「待 Frank 处理」F7）")
    return e


def base(e):
    return "https://api.ig.com/gateway/deal" if e["env"] == "live" else "https://demo-api.ig.com/gateway/deal"


def _req(method, path, body=None, version=1, auth=True, retry=True):
    orig = method
    e = env()
    if auth and (not _S["cst"] or _S["env"] != e["env"] or time.time() - _S["t"] > 5 * 3600):
        login(e)
    h = {"X-IG-API-KEY": e["key"], "Accept": "application/json; charset=UTF-8",
         "Content-Type": "application/json; charset=UTF-8", "Version": str(version)}
    if auth:
        h.update({"CST": _S["cst"], "X-SECURITY-TOKEN": _S["xst"]})
    if method == "DELETE":        # IG wants DELETE-with-body sent as POST + _method header
        h["_method"] = "DELETE"
        method = "POST"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base(e) + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw or b"{}"), resp.headers
    except urllib.error.HTTPError as err:
        txt = err.read().decode("utf-8", "ignore")[:300]
        if auth and retry and err.code in (401, 403) and "token" in txt.lower():
            _S["cst"] = None
            return _req(orig, path, body, version, auth, False)
        try:
            code = json.loads(txt).get("errorCode", txt)
        except Exception:
            code = txt
        raise RuntimeError(f"IG 返回错误：{_cn(code)}") from None


def _cn(code):
    m = {"error.security.invalid-details": "用户名或密码不对（demo 和真账户的登录名不一样）",
         "error.security.api-key-invalid": "API Key 不对，或者这个 Key 是给另一种账户（demo/真账户）的",
         "error.security.api-key-disabled": "这个 API Key 被停用了，去 IG 网站重新生成",
         "error.security.account-suspended": "IG 账户被暂停",
         "error.public-api.exceeded-api-key-allowance": "调用太频繁，等一分钟再试",
         "error.trading.otc.market-orders.not-supported-for-epic": "这个品种不能下市价单"}
    return m.get(str(code), str(code))


def login(e=None):
    e = e or env()
    _S.update(cst=None, xst=None)
    body, hd = _req("POST", "/session", {"identifier": e["user"], "password": e["pw"]}, version=2, auth=False)
    _S.update(cst=hd.get("CST"), xst=hd.get("X-SECURITY-TOKEN"), t=time.time(), env=e["env"],
              acc=body.get("currentAccountId"))
    if e["acc"] and e["acc"] != _S["acc"]:
        _req("PUT", "/session", {"accountId": e["acc"], "defaultAccount": None}, version=1)
        _S["acc"] = e["acc"]
    return {"env": e["env"], "account": _S["acc"]}


def epic(code):
    """BTC -> IG epic (searched once, cached in local/ig_epics.json)."""
    code = code.upper()
    try:
        cache = json.loads(EPICS.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    key = f"{_S['env'] or env()['env']}:{code}"
    if key in cache:
        return cache[key]
    name = NAMES.get(code, code)
    res, _ = _req("GET", "/markets?searchTerm=" + urllib.request.quote(name))
    cands = [m for m in res.get("markets", []) if m.get("expiry") in ("-", "DFB")
             and name.lower() in (m.get("instrumentName") or "").lower()]
    cands.sort(key=lambda m: (("mini" in m["instrumentName"].lower()), len(m["instrumentName"])))
    if not cands:
        raise RuntimeError(f"IG 上找不到 {code} 的差价合约")
    cache[key] = {"epic": cands[0]["epic"], "name": cands[0]["instrumentName"]}
    T.LOCAL.mkdir(exist_ok=True)
    EPICS.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache[key]


def check(order):
    o = {"mkt": "cx", "code": str(order.get("code", "")).upper().replace("USDT", "")}
    if not o["code"].isalnum() or len(o["code"]) > 10:
        raise ValueError("代码不正确")
    o["side"] = str(order.get("side", "")).upper()
    if o["side"] not in ("BUY", "SELL"):
        raise ValueError("方向只能是买涨或卖空")
    try:
        o["qty"] = float(order.get("qty"))
    except (TypeError, ValueError):
        raise ValueError("数量要填数字") from None
    if not 0 < o["qty"] <= 1000:
        raise ValueError("数量不对")
    stop = order.get("stop")
    try:
        o["stop"] = float(stop) if stop not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        raise ValueError("止损距离要填数字") from None
    if o["stop"] is not None and o["stop"] <= 0:
        raise ValueError("止损距离要大于 0")
    o["price"] = 0   # market order; kept for the shared token format
    return o


def _market(o):
    ep = epic(o["code"])
    m, _ = _req("GET", "/markets/" + ep["epic"], version=3)
    ins, snap, rules = m.get("instrument", {}), m.get("snapshot", {}), m.get("dealingRules", {})
    ccy = next((c for c in ins.get("currencies", []) if c.get("isDefault")), (ins.get("currencies") or [{}])[0])
    mf = ins.get("marginFactor")
    try:
        mf = float(mf) / 100 if ins.get("marginFactorUnit") == "PERCENTAGE" else None
    except (TypeError, ValueError):
        mf = None
    return ep, ins, snap, rules, ccy, mf


def preview(order):
    o = check(order)
    e = env()
    ep, ins, snap, rules, ccy, mf = _market(o)
    bid, offer = snap.get("bid"), snap.get("offer")
    px = offer if o["side"] == "BUY" else bid
    if not px:
        raise RuntimeError(f"IG 现在没有报价（市场状态：{snap.get('marketStatus')}）")
    csize = float(ins.get("contractSize") or 1)
    vpp = float(ins.get("valueOfOnePoint") or csize)       # money per 1 point move per 1 size
    notional = o["qty"] * vpp * px
    margin = notional * mf if mf else None
    mins = (rules.get("minDealSize") or {}).get("value")
    warn = []
    if mins and o["qty"] < float(mins):
        warn.append(f"IG 这个品种最少下 {mins}")
    if bid and offer:
        sp = (offer - bid) / ((offer + bid) / 2) * 100
        warn.append(f"买卖价差 {sp:.2f}%（一进一出先亏这么多）")
    loss_at_stop = o["qty"] * vpp * o["stop"] if o["stop"] else None
    if o["stop"]:
        md = (rules.get("minNormalStopOrLimitDistance") or {}).get("value")
        if md and o["stop"] < float(md):
            warn.append(f"止损距离太近：IG 要求至少 {md} 点")
    else:
        warn.append("没设止损：行情反向时只能靠 IG 的保证金强平（保证金用光会自动平仓）")
    out = {**o, "env": e["env"], "paper": e["env"] != "live", "epic": ep["epic"], "name": ep["name"],
           "bid": bid, "ask": offer, "price_est": px, "ccy": ccy.get("code", ""), "notional": round(notional, 2),
           "margin": round(margin, 2) if margin else None, "leverage": round(1 / mf, 1) if mf else None,
           "loss_at_stop": round(loss_at_stop, 2) if loss_at_stop else None, "status": snap.get("marketStatus"),
           "warnings": warn, "token": T._token(o), "value": round(notional, 2)}
    T.log({"action": "preview", "broker": "IG", **{k: out[k] for k in ("code", "side", "qty", "stop", "env", "notional")}})
    return out


def place(order, token):
    o = check(order)
    hit = T._tokens.pop(token or "", None)
    if not hit or time.time() - hit[0] > 120 or hit[1] != {k: o[k] for k in ("mkt", "code", "side", "qty", "price")}:
        raise ValueError("确认已过期或订单改过了，请重新预览")
    e = env()
    s = T.settings()
    if e["env"] == "live" and not s.get("trade_live"):
        raise ValueError("真账户下单没打开：⚙ 设置里打开「真账户下单」并输入确认语")
    today = time.strftime("%Y-%m-%d")
    try:
        n_today = sum(1 for r in json.loads(T.LOG.read_text(encoding="utf-8")) if r.get("action") == "place" and r.get("time", "").startswith(today))
    except Exception:
        n_today = 0
    if n_today >= int(s.get("max_orders_day") or 20):
        raise ValueError("今天下单次数到上限了（⚙ 设置里改）")
    ep, ins, snap, rules, ccy, mf = _market(o)
    body = {"epic": ep["epic"], "expiry": ins.get("expiry") or "-", "direction": o["side"], "size": o["qty"],
            "orderType": "MARKET", "timeInForce": "FILL_OR_KILL", "guaranteedStop": False,
            "forceOpen": True, "currencyCode": ccy.get("code"), "dealReference": "neon" + str(int(time.time()))}
    if o["stop"]:
        body["stopDistance"] = o["stop"]
    res, _ = _req("POST", "/positions/otc", body, version=2)
    ref = res.get("dealReference")
    conf = {}
    for _ in range(6):
        time.sleep(0.5)
        try:
            conf, _ = _req("GET", "/confirms/" + ref)
            if conf.get("dealStatus"):
                break
        except Exception:
            pass
    out = {**o, "env": e["env"], "paper": e["env"] != "live", "deal_ref": ref, "status": conf.get("dealStatus", "已提交"),
           "reason": conf.get("reason"), "level": conf.get("level"), "deal_id": conf.get("dealId")}
    T.log({"action": "place", "broker": "IG", **{k: out[k] for k in ("code", "side", "qty", "stop", "env", "status", "level", "deal_id")}})
    if out["status"] == "REJECTED":
        raise RuntimeError(f"IG 拒绝了这笔单：{out['reason']}")
    return out


def close(deal_id):
    pos, _ = _req("GET", "/positions", version=2)
    p = next((x for x in pos.get("positions", []) if x["position"]["dealId"] == deal_id), None)
    if not p:
        raise RuntimeError("找不到这个持仓（可能已经平了）")
    body = {"dealId": deal_id, "direction": "SELL" if p["position"]["direction"] == "BUY" else "BUY",
            "size": p["position"]["size"], "orderType": "MARKET", "timeInForce": "FILL_OR_KILL"}
    res, _ = _req("DELETE", "/positions/otc", body)
    T.log({"action": "close", "broker": "IG", "deal_id": deal_id, "ref": res.get("dealReference")})
    return {"closed": deal_id, "ref": res.get("dealReference")}


def overview():
    e = env()
    acc, _ = _req("GET", "/accounts")
    a = next((x for x in acc.get("accounts", []) if x.get("accountId") == _S["acc"]), (acc.get("accounts") or [{}])[0])
    bal = a.get("balance") or {}
    pos, _ = _req("GET", "/positions", version=2)
    rows = []
    for x in pos.get("positions", []):
        p, mk = x["position"], x["market"]
        cur = mk.get("bid") if p["direction"] == "BUY" else mk.get("offer")
        pnl = (cur - p["level"]) * p["size"] * (1 if p["direction"] == "BUY" else -1) * float(p.get("contractSize") or 1) if cur else None
        rows.append({"deal_id": p["dealId"], "name": mk.get("instrumentName"), "side": p["direction"], "size": p["size"],
                     "level": p["level"], "now": cur, "stop": p.get("stopLevel"), "pnl": round(pnl, 2) if pnl is not None else None,
                     "ccy": p.get("currency")})
    return {"broker": "IG", "env": e["env"], "paper": e["env"] != "live", "account": _S["acc"], "ccy": a.get("currency"),
            "balance": bal.get("balance"), "available": bal.get("available"), "pnl": bal.get("profitLoss"),
            "deposit": bal.get("deposit"), "positions": rows, "live_on": bool(T.settings().get("trade_live"))}


def save_creds(key, user, pw, mode):
    """From ⚙ 设置 (Frank types them himself). Empty fields keep the old value. Then log in once to test."""
    f = T.LOCAL / "ig_creds.json"
    try:
        cur = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    for k, v in (("IG_API_KEY", key), ("IG_USERNAME", user), ("IG_PASSWORD", pw)):
        if v:
            cur[k] = str(v).strip()
    if mode in ("demo", "live"):
        cur["IG_ENV"] = mode
    T.LOCAL.mkdir(exist_ok=True)
    f.write_text(json.dumps(cur), encoding="utf-8")
    _S["cst"] = None
    T.log({"action": "settings", "broker": "IG", "env": cur.get("IG_ENV", "demo")})
    return login()


def creds_status():
    try:
        e = env()
        return {"set": True, "env": e["env"], "user": e["user"][:2] + "***"}
    except Exception:
        return {"set": False}
